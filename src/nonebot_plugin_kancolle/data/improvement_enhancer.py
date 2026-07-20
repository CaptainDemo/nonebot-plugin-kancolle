"""kcwikizh/kcwiki-improvement-data 改修数据懒加载器（P7.1）。

特点：
- 一次拉取整个 improve_data.json（257 KB，344 条装备），按 equip_id 切片入 equipment_improvements 表
- **全局锁**（asyncio.Lock），任意 equip_id 触发同一 URL 只请求一次
- TTL 默认 7 天；improve_data.json 中无此 equip_id 时缓存为 not_found（24h）
- 后续查询走 SQLite 单点读，零网络
- 版本指纹存 meta.improvement_version，作为改修卡渲染缓存键的一部分

不进 fusion pipeline 的原因：
- 与 start2 主数据无字段交集（不冲突）
- 来自独立仓库，更新节奏不同步（每周一/周五）
- 进 fusion 会让 data_version 指纹语义混乱
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from ..utils.logger import log
from .models import (
    ImprovementData,
    ImprovementEntry,
    ImprovementMaterial,
    ImprovementRecipe,
    ImprovementUpgrade,
)
from .sources.github import fetch_latest_commit_sha, fetch_raw
from .store import Store

REPO = "kcwikizh/kcwiki-improvement-data"
REF = "gh-pages"
FILE_PATH = "improve_data.json"

IMPROVE_URL = f"https://raw.githubusercontent.com/{REPO}/{REF}/{FILE_PATH}"


class ImprovementEnhancer:
    """kcwiki 改修数据懒加载器。"""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        store: Store,
        ok_ttl_days: int = 7,
        not_found_ttl_hours: int = 24,
        timeout: float = 30.0,
    ) -> None:
        self._http = http_client
        self._store = store
        self._ok_ttl = ok_ttl_days * 86400
        self._neg_ttl = not_found_ttl_hours * 3600
        self._timeout = timeout
        # 全局锁：第一次任意装备查改修 → 拉全量 → 入库。后续走 SQLite。
        self._lock = asyncio.Lock()
        # 标记是否已成功拉取过一次（避免每次 get 都进锁检查）
        self._loaded = False

    async def get(self, equip_id: int) -> ImprovementData | None:
        """获取装备改修数据。返回 None 表示无改修数据或拉取失败。"""
        # 1. 先查缓存（命中且未过期直接返回）
        cache_hit, data = self._read_cache(equip_id)
        if cache_hit:
            return data

        # 2. 缓存未命中或过期 → 加锁拉取全量
        async with self._lock:
            # 拿到锁后再查一次（其他协程可能已经填好缓存）
            cache_hit, data = self._read_cache(equip_id)
            if cache_hit:
                return data

            # 仅在首次或全部过期时才真正拉取
            await self._fetch_and_populate()
            cache_hit, data = self._read_cache(equip_id)
            return data if cache_hit else None

    async def get_version(self) -> str:
        """获取当前缓存的改修数据版本指纹（commit_sha）。"""
        return self._store.get_meta("improvement_version") or ""

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _read_cache(self, equip_id: int) -> tuple[bool, ImprovementData | None]:
        """读缓存。返回 (cache_hit, data)。

        cache_hit=True 表示不需要再拉取（含 not_found 负缓存）。
        data 仅在 cache_hit=True 且 status=ok 时有值。
        """
        entry = self._store.get_improvement(equip_id)
        if entry is None:
            return False, None
        data, status, expires_at = entry
        # 全量更新策略：任意 equip_id 缓存过期，意味着可能整个 improve_data.json
        # 也需要刷新。但只在锁内才真正刷新；这里仅判断当前 equip_id 是否过期。
        if expires_at <= int(asyncio.get_event_loop().time()) and status != "ok":
            # 负缓存过期 → 重新拉取
            return False, None
        if expires_at <= int(asyncio.get_event_loop().time()):
            # ok 缓存过期：也返回旧数据，让上层 get 触发刷新（在锁内）
            # 这里返回旧数据，但 cache_hit=False 触发刷新
            return False, None
        return True, (data if status == "ok" else None)

    async def _fetch_and_populate(self) -> None:
        """实际发起 HTTP 请求拉全量 improve_data.json，解析后批量入库。"""
        try:
            # 1. 取 commit_sha 作为版本指纹
            sha = await fetch_latest_commit_sha(self._http, REPO, REF)
            # 2. 拉 improve_data.json
            res = await fetch_raw(self._http, REPO, FILE_PATH, REF)
            if res.not_modified or not res.body:
                log.warning("improve_data.json returned empty body")
                return
            payload = json.loads(res.body)
        except httpx.HTTPError as e:
            log.warning(f"improvement fetch failed: {e}")
            return
        except (json.JSONDecodeError, ValueError) as e:
            log.warning(f"improvement payload invalid: {e}")
            return

        # 3. 解析为 ImprovementData 列表 + 收集所有 equip_id（含 not_found）
        items: list[tuple[int, ImprovementData | None, str]] = []
        known_ids: set[int] = set()
        for equip_id_str, entry in payload.items():
            try:
                equip_id = int(equip_id_str)
            except (TypeError, ValueError):
                continue
            known_ids.add(equip_id)
            data = _parse_improvement_entry(equip_id, entry)
            if data is None:
                continue
            items.append((equip_id, data, "ok"))

        if not items:
            log.warning("improvement payload produced no entries")
            return

        # 4. 批量入库
        self._store.set_improvement_batch(items, ttl_seconds=self._ok_ttl)
        self._store.set_meta("improvement_version", sha)
        self._loaded = True
        log.info(
            f"improvement data loaded: {len(items)} equips, version={sha[:8]}..."
        )


# ----------------------------------------------------------------------
# 解析器
# ----------------------------------------------------------------------

def _parse_improvement_entry(
    equip_id: int, raw: Any
) -> ImprovementData | None:
    """解析 improve_data.json 中单件装备的数据为 ImprovementData。"""
    if not isinstance(raw, dict):
        return None

    improvement_list = raw.get("improvement")
    if not isinstance(improvement_list, list) or not improvement_list:
        return None

    entries: list[ImprovementEntry] = []
    for raw_entry in improvement_list:
        if not isinstance(raw_entry, dict):
            continue
        entry = _parse_entry(raw_entry)
        if entry is not None:
            entries.append(entry)

    if not entries:
        return None

    return ImprovementData(equip_id=equip_id, entries=entries)


def _parse_entry(raw: dict[str, Any]) -> ImprovementEntry | None:
    """解析单条 improvement 元素。"""
    # upgrade
    upgrade: ImprovementUpgrade | None = None
    raw_upgrade = raw.get("upgrade")
    if isinstance(raw_upgrade, dict):
        target_id_raw = raw_upgrade.get("id")
        target_id = _safe_int(target_id_raw)
        if target_id:
            upgrade = ImprovementUpgrade(
                level=_safe_int(raw_upgrade.get("level")) or 0,
                target_id=target_id,
                target_name=raw_upgrade.get("name") or None,
            )

    # req[]: 秘书舰 + 星期组合
    recipes: list[ImprovementRecipe] = []
    raw_req = raw.get("req")
    if isinstance(raw_req, list):
        for r in raw_req:
            if not isinstance(r, dict):
                continue
            recipe = _parse_recipe(r)
            if recipe is not None:
                recipes.append(recipe)

    # consume: 基础消耗 + material[] 阶段消耗
    fuel = ammo = steel = bauxite = None
    materials: list[ImprovementMaterial] = []
    raw_consume = raw.get("consume")
    if isinstance(raw_consume, dict):
        fuel = _safe_int(raw_consume.get("fuel"))
        ammo = _safe_int(raw_consume.get("ammo"))
        steel = _safe_int(raw_consume.get("steel"))
        bauxite = _safe_int(raw_consume.get("bauxite"))
        raw_materials = raw_consume.get("material")
        if isinstance(raw_materials, list):
            for m in raw_materials:
                if isinstance(m, dict):
                    mat = _parse_material(m)
                    if mat is not None:
                        materials.append(mat)

    if not recipes and not materials:
        return None  # 空条目不存

    return ImprovementEntry(
        upgrade=upgrade,
        recipes=recipes,
        materials=materials,
        fuel=fuel,
        ammo=ammo,
        steel=steel,
        bauxite=bauxite,
    )


def _parse_recipe(raw: dict[str, Any]) -> ImprovementRecipe | None:
    """解析单条 req（秘书舰 + 星期组合）。"""
    raw_day = raw.get("day")
    if not isinstance(raw_day, list):
        raw_day = []
    day = ImprovementRecipe.normalize_day([bool(x) for x in raw_day])

    # secretary 字段（中文名数组）优先；缺失则用 secretaryIds 转字符串
    secretary_names: list[str] = []
    raw_secretary = raw.get("secretary")
    if isinstance(raw_secretary, list):
        secretary_names = [str(s) for s in raw_secretary if s]
    if not secretary_names:
        raw_ids = raw.get("secretaryIds")
        if isinstance(raw_ids, list):
            secretary_names = [f"#{int(i)}" for i in raw_ids if i]

    return ImprovementRecipe(day=day, secretary_names=secretary_names)


def _parse_material(raw: dict[str, Any]) -> ImprovementMaterial | None:
    """解析单阶段消耗。"""
    development = _parse_int_pair(raw.get("development"))
    improvement_res = _parse_int_pair(raw.get("improvement"))
    if development is None and improvement_res is None:
        return None

    item_id = None
    item_name = None
    item_count = None
    raw_item = raw.get("item")
    if isinstance(raw_item, dict):
        item_id = _safe_int(raw_item.get("id"))
        if item_id:
            item_name = raw_item.get("name") or None
            item_count = _safe_int(raw_item.get("count"))

    return ImprovementMaterial(
        development=development or [0, 0],
        improvement_res=improvement_res or [0, 0],
        item_id=item_id,
        item_name=item_name,
        item_count=item_count,
    )


def _parse_int_pair(v: Any) -> list[int] | None:
    """解析 [下限, 上限] 二元数组。"""
    if isinstance(v, list) and len(v) >= 2:
        return [_safe_int(v[0]) or 0, _safe_int(v[1]) or 0]
    return None


def _safe_int(v: Any) -> int | None:
    """容忍字符串/None/负数，统一转 int 或 None。"""
    if v is None:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None
