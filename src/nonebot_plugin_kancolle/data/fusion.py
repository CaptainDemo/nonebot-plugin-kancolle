"""多源数据融合管线。

流程：
1. 并发拉取所有启用源的 RawData（任一源失败不阻塞其他源）
2. 用 kc3 输出构建 JP名 -> {cn, en} 查找表
3. 遍历 kcanotify 主数据，按 JP 名合并 cn/en 名字
4. 反向回溯 + 链头计算，填充 remodel_from / remodel_chain_root
5. 批量写入主表 + 重建 FTS5 索引 + 更新 sources / meta
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from ..utils.logger import log
from .models import Ship, ShipName, ShipStats
from .sources.base import SourceAdapter
from .store import Store


async def run_fusion(
    store: Store,
    adapters: list[SourceAdapter],
    http_client: httpx.AsyncClient,
) -> str:
    """执行一次完整 fusion。返回 data_version 指纹（用于缓存失效）。"""
    # 1. 并发拉取
    raws: dict[str, Any] = {}
    fetch_tasks = {adapter.name: adapter.fetch(http_client) for adapter in adapters}
    results = await asyncio.gather(
        *fetch_tasks.values(), return_exceptions=True
    )
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

    # 2. 构建 kc3 名字查找表
    name_lookup: dict[str, dict[str, str | None]] = {}
    kc3_adapter = next((a for a in adapters if a.name == "kc3"), None)
    if kc3_adapter and "kc3" in raws:
        for item in kc3_adapter.normalize_ships(raws["kc3"]):
            jp = item.get("lookup_jp_name")
            if jp:
                # 仅当至少有一个非空翻译时才记录
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

    # 7. 更新 sources 表（成功的源）
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
