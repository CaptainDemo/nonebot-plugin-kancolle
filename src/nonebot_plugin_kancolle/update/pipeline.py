"""完整数据更新流程：fusion + 缓存失效 + 状态摘要。

调用方负责构造 adapters / http_client / renderer；本模块只做编排。
这样便于：
- 单元测试：传 in-memory store + fake adapters + None renderer
- 实际运行：从 bootstrap 取真实单例
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from ..data.fusion import run_fusion
from ..data.sources.base import SourceAdapter
from ..data.store import Store
from ..utils.logger import log

if TYPE_CHECKING:
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
    cache_invalidated: int  # 失效的图片缓存数；0 表示未失效或无需失效
    sources: list[SourceStatus]
    error: str | None  # 非 None 表示流程异常（部分源失败仍算成功）


async def run_update_pipeline(
    store: Store,
    adapters: list[SourceAdapter],
    http_client: httpx.AsyncClient,
    renderer: "ShipRenderer | None" = None,
) -> UpdateResult:
    """执行完整更新：fusion → 比对版本 → 失效缓存 → 返回摘要。

    异常处理：
    - 单源失败由 fusion 内部捕获，记为该源 status=failed
    - 全部源失败由 fusion 抛 RuntimeError，本函数包装为 UpdateResult.error
    - renderer 缓存失效失败不阻塞流程（仅 log warning）
    """
    old_version = store.get_meta("data_version") or ""

    try:
        new_version = await run_fusion(store, adapters, http_client)
    except Exception as e:
        log.error(f"update pipeline failed: {e}")
        return UpdateResult(
            data_version=old_version,
            changed=False,
            ship_count=store.count_ships(),
            cache_invalidated=0,
            sources=_collect_source_statuses(store),
            error=str(e),
        )

    changed = new_version != old_version
    cache_invalidated = 0
    if changed and renderer is not None:
        try:
            cache_invalidated = renderer.invalidate_cache()
        except Exception as e:
            log.warning(f"cache invalidation failed: {e}")

    return UpdateResult(
        data_version=new_version,
        changed=changed,
        ship_count=store.count_ships(),
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
