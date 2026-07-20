"""多源数据融合管线。

主入口：
- ``run_fusion``: 舰娘融合。接收可选的 raws 参数避免重复网络请求
- ``run_equipment_fusion``: 装备融合。**必须**接收已拉取的 raws（与舰娘共享拉取）

两个 fusion 共享拉取阶段（``_fetch_all_adapters``）：
1. 并发拉取所有启用源的 RawData（任一源失败不阻塞其他源）
2. 失败的源记 status=failed；成功的源在 raws 中保留

舰娘 fusion 流程（``run_fusion``）：
1. 用 kc3 输出构建 JP名 -> {cn, en} 查找表
2. 遍历 kcanotify 主数据，按 JP 名合并 cn/en 名字
3. 反向回溯 + 链头计算，填充 remodel_from / remodel_chain_root
4. 批量写入主表 + 重建 FTS5 索引 + 更新 sources / meta

装备 fusion 流程（``run_equipment_fusion``）：
1. 用 kc3 items.json 构建 JP名 -> {cn, en} 装备名查找表
2. 用 kc3 equiptype.json[3] + kcanotify api_mst_slotitem_equiptype 构建类型查找表
3. 写入 equipment_types 表
4. 遍历 kcanotify api_mst_slotitem，按 JP 名合并翻译
5. 批量写入 equipments + 重建 FTS5 索引
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from ..utils.logger import log
from .models import Equipment, EquipmentName, EquipmentStats, Ship, ShipName, ShipStats
from .sources.base import RawData, SourceAdapter
from .store import Store


async def _fetch_all_adapters(
    adapters: list[SourceAdapter],
    http_client: httpx.AsyncClient,
    store: Store,
) -> dict[str, RawData]:
    """并发拉取所有启用源。

    - 任一源失败：记 status=failed，不阻塞其他源
    - 全部失败：抛 RuntimeError
    - 成功的源返回在 dict 中，键为 adapter.name
    """
    raws: dict[str, RawData] = {}
    fetch_tasks = {adapter.name: adapter.fetch(http_client) for adapter in adapters}
    results = await asyncio.gather(*fetch_tasks.values(), return_exceptions=True)
    for (name, _), result in zip(fetch_tasks.items(), results, strict=True):
        if isinstance(result, Exception):
            log.error(f"source {name} fetch failed: {result}")
            store.record_source(
                name=name,
                version="",
                fetched_at=int(time.time()),
                item_count=0,
                status="failed",
                error_msg=repr(result)[:500],
            )
        else:
            raws[name] = result
            log.info(f"source {name} fetched: version={result.version}")

    if not raws:
        raise RuntimeError("no source fetched successfully; aborting fusion")
    return raws


async def run_fusion(
    store: Store,
    adapters: list[SourceAdapter],
    http_client: httpx.AsyncClient,
    raws: dict[str, RawData] | None = None,
) -> str:
    """执行一次完整舰娘 fusion。返回 data_version 指纹（用于缓存失效）。

    参数：
        raws: 已拉取的源数据。None 时本函数内部调用 _fetch_all_adapters
              （向后兼容旧调用方与测试）。传入时跳过拉取，便于与装备
              fusion 共享同一次网络请求。
    """
    # 1. 拉取（若调用方未提供 raws）
    if raws is None:
        raws = await _fetch_all_adapters(adapters, http_client, store)

    # 2. 构建 kc3 名字查找表
    name_lookup: dict[str, dict[str, str | None]] = {}
    kc3_adapter = next((a for a in adapters if a.name == "kc3"), None)
    if kc3_adapter and "kc3" in raws:
        for item in kc3_adapter.normalize_ships(raws["kc3"]):
            jp = item.get("lookup_jp_name")
            if jp:
                cn = item.get("name", {}).get("cn")
                en = item.get("name", {}).get("en")
                if cn or en:
                    name_lookup[jp] = {"cn": cn, "en": en}
        log.info(f"kc3 name lookup built: {len(name_lookup)} entries")

    # 3. 用 kcanotify 主数据合并
    merged: dict[int, Ship] = {}
    kcanotify_adapter = next((a for a in adapters if a.name == "kcanotify"), None)
    if kcanotify_adapter and "kcanotify" in raws:
        kc3_version = raws["kc3"].version if "kc3" in raws else ""
        kc3_fetched_at = raws["kc3"].fetched_at if "kc3" in raws else 0
        for item in kcanotify_adapter.normalize_ships(raws["kcanotify"]):
            ship = _build_ship(item, name_lookup, kc3_version, kc3_fetched_at)
            merged[ship.id] = ship
        log.info(f"kcanotify normalized: {len(merged)} ships")

    if not merged:
        raise RuntimeError("fusion produced no ships; check kcanotify adapter")

    # 4. 计算改造链
    _compute_remodel_chains(merged)
    log.info("remodel chains computed")

    # 5. 写入 store
    store.write_ships(list(merged.values()))
    store.rebuild_fts()

    # 6. 计算 data_version 指纹
    data_version = "|".join(f"{n}={r.version}" for n, r in sorted(raws.items()))
    store.set_meta("data_version", data_version)

    # 7. 更新 sources 表（成功源）
    for name, raw in raws.items():
        store.record_source(
            name=name,
            version=raw.version,
            fetched_at=raw.fetched_at,
            item_count=len(merged) if name == "kcanotify" else 0,
            status="ok",
            error_msg="",
        )

    log.info(f"fusion done: {len(merged)} ships, data_version={data_version}")
    return data_version


async def run_equipment_fusion(
    store: Store,
    adapters: list[SourceAdapter],
    raws: dict[str, RawData],
) -> int:
    """执行装备 fusion。返回写入装备条数。

    与 ``run_fusion`` 不同：本函数**必须接收已拉取的 raws**（由调用方共享），
    不再重复发起 HTTP 请求。

    任一源缺失或失败均不阻塞流程，仅减少覆盖范围。
    """
    # 1. 构建 kc3 装备名查找表（JP -> {cn, en}）
    name_lookup: dict[str, dict[str, str | None]] = {}
    kc3_adapter = next((a for a in adapters if a.name == "kc3"), None)
    if kc3_adapter and "kc3" in raws:
        for item in kc3_adapter.normalize_slotitems(raws["kc3"]):
            jp = item.get("lookup_jp_name")
            if jp:
                cn = item.get("name", {}).get("cn")
                en = item.get("name", {}).get("en")
                if cn or en:
                    name_lookup[jp] = {"cn": cn, "en": en}
        log.info(f"kc3 equipment name lookup built: {len(name_lookup)} entries")

    # 2. 装备类型：合并 kcanotify JP 名 + kc3 cn/en 翻译
    type_lookup: dict[int, dict[str, str | None]] = {}
    # 先填 kcanotify 的 JP 名（兜底）
    kcanotify_adapter = next((a for a in adapters if a.name == "kcanotify"), None)
    if kcanotify_adapter and "kcanotify" in raws:
        for t in kcanotify_adapter.normalize_equiptypes(raws["kcanotify"]):
            type_id = t.get("type_id")
            if type_id is not None:
                type_lookup.setdefault(int(type_id), {}).update({
                    "jp": t.get("name_jp"),
                    "cn": None,
                    "en": None,
                })
    # 再用 kc3 覆盖 cn/en
    if kc3_adapter and "kc3" in raws:
        for t in kc3_adapter.normalize_equiptypes(raws["kc3"]):
            type_id = t.get("type_id")
            if type_id is not None:
                entry = type_lookup.setdefault(int(type_id), {})
                if t.get("name_cn"):
                    entry["cn"] = t["name_cn"]
                if t.get("name_en"):
                    entry["en"] = t["name_en"]

    # 3. 写入 equipment_types 表
    type_rows = [
        {
            "type_id": tid,
            "name_jp": v.get("jp"),
            "name_cn": v.get("cn"),
            "name_en": v.get("en"),
        }
        for tid, v in sorted(type_lookup.items())
    ]
    store.write_equipment_types(type_rows)
    log.info(f"equipment_types written: {len(type_rows)}")

    # 4. 用 kcanotify 主数据合并装备
    merged_equip: dict[int, Equipment] = {}
    if kcanotify_adapter and "kcanotify" in raws:
        kc3_version = raws["kc3"].version if "kc3" in raws else ""
        kc3_fetched_at = raws["kc3"].fetched_at if "kc3" in raws else 0
        for item in kcanotify_adapter.normalize_slotitems(raws["kcanotify"]):
            equip = _build_equipment(item, name_lookup, kc3_version, kc3_fetched_at)
            merged_equip[equip.id] = equip
        log.info(f"kcanotify equipment normalized: {len(merged_equip)} items")

    if not merged_equip:
        log.warning("equipment fusion produced no items; check kcanotify adapter")
        return 0

    # 5. 写入 store
    store.write_equipments(list(merged_equip.values()))
    store.rebuild_equipment_fts()

    log.info(f"equipment fusion done: {len(merged_equip)} items")
    return len(merged_equip)


# ----------------------------------------------------------------------
# 内部辅助
# ----------------------------------------------------------------------

def _build_ship(
    item: dict[str, Any],
    name_lookup: dict[str, dict[str, str | None]],
    kc3_version: str,
    kc3_fetched_at: int,
) -> Ship:
    """把 kcanotify 规整后的 dict 转 Ship，并合并 kc3 翻译。"""
    jp_name = item.get("name", {}).get("jp")
    cn_name: str | None = None
    en_name: str | None = None
    if jp_name and jp_name in name_lookup:
        entry = name_lookup[jp_name]
        cn_name = entry.get("cn")
        en_name = entry.get("en")

    name = ShipName(
        jp=jp_name,
        cn=cn_name,
        en=en_name,
        romaji=item.get("name", {}).get("romaji"),
    )

    stats_base = ShipStats(**item.get("stats_base", {}))
    stats_max = ShipStats(**item.get("stats_max", {}))

    # 合并 provenance：补 kc3 的 name_cn / name_en 来源
    provenance = dict(item.get("provenance", {}))
    if cn_name:
        provenance["name_cn"] = {
            "source": "kc3",
            "version": kc3_version,
            "fetched_at": kc3_fetched_at,
        }
    if en_name:
        provenance["name_en"] = {
            "source": "kc3",
            "version": kc3_version,
            "fetched_at": kc3_fetched_at,
        }

    return Ship(
        id=item["id"],
        name=name,
        ship_type_id=item.get("ship_type_id"),
        ship_class_id=item.get("ship_class_id"),
        ship_class_jp=item.get("ship_class_jp"),
        speed=item.get("speed"),
        range_=item.get("range_"),
        stats_base=stats_base,
        stats_max=stats_max,
        remodel_to=item.get("remodel_to"),
        remodel_level=item.get("remodel_level"),
        remodel_fuel_cost=item.get("remodel_fuel_cost"),
        remodel_ammo_cost=item.get("remodel_ammo_cost"),
        provenance=provenance,
    )


def _build_equipment(
    item: dict[str, Any],
    name_lookup: dict[str, dict[str, str | None]],
    kc3_version: str,
    kc3_fetched_at: int,
) -> Equipment:
    """把 kcanotify 规整后的 dict 转 Equipment，并合并 kc3 翻译。"""
    jp_name = item.get("name", {}).get("jp")
    cn_name: str | None = None
    en_name: str | None = None
    if jp_name and jp_name in name_lookup:
        entry = name_lookup[jp_name]
        cn_name = entry.get("cn")
        en_name = entry.get("en")

    name = EquipmentName(jp=jp_name, cn=cn_name, en=en_name)
    stats = EquipmentStats(**item.get("stats", {}))

    # 合并 provenance：补 kc3 的 name_cn / name_en 来源
    provenance = dict(item.get("provenance", {}))
    if cn_name:
        provenance["name_cn"] = {
            "source": "kc3",
            "version": kc3_version,
            "fetched_at": kc3_fetched_at,
        }
    if en_name:
        provenance["name_en"] = {
            "source": "kc3",
            "version": kc3_version,
            "fetched_at": kc3_fetched_at,
        }

    return Equipment(
        id=item["id"],
        name=name,
        type_icon_id=item.get("type_icon_id"),
        type_id=item.get("type_id"),
        rarity=item.get("rarity"),
        range_=item.get("range_"),
        stats=stats,
        distance=item.get("distance"),
        cost=item.get("cost"),
        broken=item.get("broken"),
        provenance=provenance,
    )


def _compute_remodel_chains(ships: dict[int, Ship]) -> None:
    """填充 remodel_from（反向回溯）与 remodel_chain_root（链头）。"""
    # 反向回溯：对每艘 ship，若它指向 remodel_to，则目标 ship 的 remodel_from 设回自身
    for ship in ships.values():
        ship.remodel_from = None
    for ship in ships.values():
        if ship.remodel_to and ship.remodel_to in ships:
            ships[ship.remodel_to].remodel_from = ship.id

    # 链头：从每艘 ship 顺着 remodel_from 一直走，seen 集合防止异常循环
    for ship in ships.values():
        current = ship
        seen: set[int] = set()
        while (
            current.remodel_from is not None
            and current.remodel_from in ships
            and current.remodel_from not in seen
        ):
            seen.add(current.id)
            current = ships[current.remodel_from]
        ship.remodel_chain_root = current.id
