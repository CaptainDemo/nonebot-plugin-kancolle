"""完整数据更新流程：fusion + 缓存失效 + 状态摘要。

调用方负责构造 adapters / http_client / renderer；本模块只做编排。
这样便于：
- 单元测试：传 in-memory store + fake adapters + None renderer
- 实际运行：从 bootstrap 取真实单例

P7 起，本管线同时跑舰娘 fusion 与装备 fusion，**共享拉取阶段**
（``_fetch_all_adapters``），避免重复网络请求。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from ..data.fusion import _fetch_all_adapters, run_equipment_fusion, run_fusion
from ..data.sources.base import SourceAdapter
from ..data.store import Store
from ..utils.logger import log

if TYPE_CHECKING:
    from ..render.equipment_renderer import EquipmentRenderer
    from ..render.renderer import ShipRenderer


@dataclass(frozen=True)
class SourceStatus:
    """单个数据源的状态摘要。"""
    name: str
    version: str
    status: str  # 'ok' / 'failed' / 'pending' / 'stale'
    fetched_at: int  # unix 秒；0 表示从未拉取
    item_count: int
    error_msg: str


@dataclass(frozen=True)
class UpdateResult:
    """一次更新流程的结果摘要。"""
    data_version: str
    changed: bool  # 与上次 data_version 是否不同
    ship_count: int
    equip_count: int
    cache_invalidated: int  # 失效的图片缓存数；0 表示未失效或无需失效
    sources: list[SourceStatus]
    error: str | None  # 非 None 表示流程异常（部分源失败仍算成功）


async def run_update_pipeline(
    store: Store,
    adapters: list[SourceAdapter],
    http_client: httpx.AsyncClient,
    ship_renderer: ShipRenderer | None = None,
    equipment_renderer: EquipmentRenderer | None = None,
) -> UpdateResult:
    """执行完整更新：fetch → ship fusion + equipment fusion → 失效缓存 → 返回摘要。

    异常处理：
    - 单源失败由 _fetch_all_adapters 内部捕获，记为该源 status=failed
    - 全部源失败由 _fetch_all_adapters 抛 RuntimeError，本函数包装为 UpdateResult.error
    - 装备 fusion 失败不阻塞舰娘 fusion（已写入的数据保留）
    - renderer 缓存失效失败不阻塞流程（仅 log warning）
    """
    old_version = store.get_meta("data_version") or ""

    # 阶段 1：共享拉取（失败源由 _fetch_all_adapters 写 sources 表）
    try:
        raws = await _fetch_all_adapters(adapters, http_client, store)
    except Exception as e:
        log.error(f"update pipeline failed at fetch: {e}")
        return UpdateResult(
            data_version=old_version,
            changed=False,
            ship_count=store.count_ships(),
            equip_count=store.count_equipments(),
            cache_invalidated=0,
            sources=_collect_source_statuses(store),
            error=str(e),
        )

    # 阶段 2：舰娘 fusion（复用 raws；失败源不重复记 sources）
    try:
        new_version = await run_fusion(store, adapters, http_client, raws=raws)
    except Exception as e:
        log.error(f"ship fusion failed: {e}")
        return UpdateResult(
            data_version=old_version,
            changed=False,
            ship_count=store.count_ships(),
            equip_count=store.count_equipments(),
            cache_invalidated=0,
            sources=_collect_source_statuses(store),
            error=str(e),
        )

    # 阶段 3：装备 fusion（复用 raws；失败不阻塞舰娘数据）
    try:
        equip_count = await run_equipment_fusion(store, adapters, raws)
    except Exception as e:
        log.warning(f"equipment fusion failed (ship data still valid): {e}")
        equip_count = store.count_equipments()

    # 阶段 4：版本对比 + 缓存失效
    changed = new_version != old_version
    cache_invalidated = 0
    if changed:
        if ship_renderer is not None:
            try:
                cache_invalidated += ship_renderer.invalidate_cache()
            except Exception as e:
                log.warning(f"ship cache invalidation failed: {e}")
        if equipment_renderer is not None:
            try:
                cache_invalidated += equipment_renderer.invalidate_cache()
            except Exception as e:
                log.warning(f"equipment cache invalidation failed: {e}")

    return UpdateResult(
        data_version=new_version,
        changed=changed,
        ship_count=store.count_ships(),
        equip_count=equip_count,
        cache_invalidated=cache_invalidated,
        sources=_collect_source_statuses(store),
        error=None,
    )


def _collect_source_statuses(store: Store) -> list[SourceStatus]:
    """从 sources 表读取所有源的状态。"""
    rows = store.list_sources()
    return [
        SourceStatus(
            name=str(r["name"]),
            version=str(r["version"] or ""),
            status=str(r["status"] or "pending"),
            fetched_at=int(r["fetched_at"] or 0),
            item_count=int(r["item_count"] or 0),
            error_msg=str(r["error_msg"] or ""),
        )
        for r in rows
    ]
