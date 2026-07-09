"""kcwiki 懒加载增强器。

按需从 api.kcwiki.moe/ship/{id} 拉取增强字段（can_drop / wiki_id / 舰种中文名），
缓存到 store.ship_enhancements 表。

特点：
- 缓存优先（TTL 默认 7 天）
- 同一 ship_id 并发请求自动去重（per-ship asyncio.Lock）
- 网络失败不缓存（避免短期内反复请求）；404 缓存 24h 避免请求不存在的 id
- 任何异常都返回 None，不阻塞主流程（查询与渲染仍可用基础数据）
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from ..utils.logger import log
from .models import ShipEnhancement
from .store import Store

KCWIKI_SHIP_URL = "https://api.kcwiki.moe/ship/{ship_id}"


class KcwikiEnhancer:
    """按需 kcwiki 增强器。"""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        store: Store,
        ttl_days: int = 7,
        not_found_ttl_hours: int = 24,
        timeout: float = 10.0,
    ) -> None:
        self._http = http_client
        self._store = store
        self._ok_ttl = ttl_days * 86400
        self._neg_ttl = not_found_ttl_hours * 3600
        self._timeout = timeout
        self._locks: dict[int, asyncio.Lock] = {}

    async def get(self, ship_id: int) -> ShipEnhancement | None:
        """获取增强数据；网络异常或 kcwiki 无此 id 时返回 None。"""
        cache_hit, data = self._read_cache(ship_id)
        if cache_hit:
            return data

        # 并发去重：同一 ship_id 同时只发一个请求
        lock = self._locks.setdefault(ship_id, asyncio.Lock())
        async with lock:
            # 拿到锁后再查一次（其他协程可能已经填好缓存）
            cache_hit, data = self._read_cache(ship_id)
            if cache_hit:
                return data
            return await self._fetch_and_cache(ship_id)

    def _read_cache(self, ship_id: int) -> tuple[bool, ShipEnhancement | None]:
        """读缓存。返回 (cache_hit, data)。

        cache_hit=True 表示不需要再拉取（含 not_found / failed 的负缓存）；
        data 仅在 cache_hit=True 且 status=ok 时有值。
        """
        entry = self._store.get_enhancement(ship_id)
        if entry is None:
            return False, None  # 无缓存
        data, status, expires_at = entry
        if expires_at <= int(time.time()):
            return False, None  # 过期，需要重拉
        # 未过期：命中（含负缓存）
        return True, (data if status == "ok" else None)

    async def _fetch_and_cache(self, ship_id: int) -> ShipEnhancement | None:
        """实际发起 HTTP 请求并写入缓存。"""
        try:
            resp = await self._http.get(
                KCWIKI_SHIP_URL.format(ship_id=ship_id),
                timeout=self._timeout,
            )
            if resp.status_code == 404:
                self._store.set_enhancement(
                    ship_id, data=None, status="not_found", ttl_seconds=self._neg_ttl
                )
                log.debug(f"kcwiki ship {ship_id}: 404, cached as not_found for 24h")
                return None
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.warning(f"kcwiki enhancement fetch failed for {ship_id}: {e}")
            return None  # 网络瞬态不缓存

        try:
            payload = resp.json()
        except json.JSONDecodeError as e:
            log.warning(f"kcwiki ship {ship_id} invalid json: {e}")
            return None

        enhancement = _parse_kcwiki_payload(ship_id, payload)
        if enhancement is None:
            self._store.set_enhancement(
                ship_id, data=None, status="failed", ttl_seconds=self._neg_ttl
            )
            return None

        self._store.set_enhancement(
            ship_id, data=enhancement, status="ok", ttl_seconds=self._ok_ttl
        )
        log.debug(
            f"kcwiki ship {ship_id} cached: can_drop={enhancement.can_drop} "
            f"wiki_id={enhancement.wiki_id}"
        )
        return enhancement


def _parse_kcwiki_payload(ship_id: int, payload: Any) -> ShipEnhancement | None:
    """从 kcwiki 响应解析出 ShipEnhancement。结构不符返回 None。"""
    if not isinstance(payload, dict):
        return None
    # kcwiki 返回的 id 应与请求一致；不一致说明响应有问题
    returned_id = payload.get("id")
    if returned_id is not None and str(returned_id) != str(ship_id):
        log.warning(
            f"kcwiki ship id mismatch: requested {ship_id}, got {returned_id}"
        )
        return None

    can_drop_raw = payload.get("can_drop")
    wiki_id_raw = payload.get("wiki_id")

    return ShipEnhancement(
        ship_id=ship_id,
        chinese_name=payload.get("chinese_name"),
        stype_name_chinese=payload.get("stype_name_chinese"),
        can_drop=bool(can_drop_raw) if can_drop_raw is not None else None,
        wiki_id=str(wiki_id_raw) if wiki_id_raw else None,
        filename=payload.get("filename"),
    )
