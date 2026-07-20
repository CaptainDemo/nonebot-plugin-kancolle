"""update/pipeline.py 单测：完整更新流程编排。

用 fake adapters（不依赖网络）+ in-memory Store 验证：
- 正常更新（changed=True，缓存失效被调用）
- 重复更新（changed=False，缓存不清）
- 全部源失败（包装为 UpdateResult.error）
- 部分源失败（仍返回成功，但 sources 标 failed）
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest

from nonebot_plugin_kancolle.data.sources.base import RawData, SourceAdapter
from nonebot_plugin_kancolle.data.store import Store
from nonebot_plugin_kancolle.update.pipeline import (
    UpdateResult,
    run_update_pipeline,
)


# ----------------------------------------------------------------------
# Fake adapters + fake renderer
# ----------------------------------------------------------------------

class FakeAdapter(SourceAdapter):
    """可控的 fake adapter，按预设 payload 返回数据。"""

    def __init__(self, name: str, payload: list[dict], version: str = "v1") -> None:
        self.name = name
        self._payload = payload
        self._version = version

    async def fetch(self, client: httpx.AsyncClient) -> RawData:
        return RawData(
            source=self.name,
            version=self._version,
            fetched_at=int(time.time()),
            payload=self._payload,
        )

    def normalize_ships(self, raw: RawData) -> Iterator[dict[str, Any]]:
        for item in raw.payload:
            yield item

    def priority(self, field: str) -> int:
        return 10 if field in {"name_jp", "stats_base"} else 1


class FailingAdapter(SourceAdapter):
    name = "fail"

    async def fetch(self, client: httpx.AsyncClient) -> RawData:
        raise RuntimeError("simulated source failure")

    def normalize_ships(self, raw: RawData) -> Iterator[dict[str, Any]]:
        return iter([])


class FakeRenderer:
    """替代 ShipRenderer，只跟踪 invalidate_cache 调用。"""

    def __init__(self) -> None:
        self.invalidated_calls: list[int | None] = []

    def invalidate_cache(self, ship_id: int | None = None) -> int:
        self.invalidated_calls.append(ship_id)
        return 42  # 假装清了 42 张图


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

def _make_payload(version: str = "v1") -> tuple[list[dict], list[dict]]:
    """构造 kcanotify + kc3 的最小 payload。"""
    kcanotify = [{
        "id": 1,
        "name": {"jp": "测试舰"},
        "stats_base": {"hp": 10},
        "provenance": {},
    }]
    kc3_payload = [{"id": None, "lookup_jp_name": "测试舰", "name": {"cn": "测试舰"}, "provenance": {}}]
    return kcanotify, kc3_payload


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    s.open()
    return s


@pytest.fixture()
def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_first_update_marks_changed(
    store: Store, http_client: httpx.AsyncClient
) -> None:
    """空库 → 第一次更新，changed=True。"""
    kcanotify, kc3 = _make_payload()
    adapters = [
        FakeAdapter("kcanotify", kcanotify, version="v1"),
        FakeAdapter("kc3", kc3, version="kc3_v1"),
    ]
    renderer = FakeRenderer()

    result = await run_update_pipeline(store, adapters, http_client, ship_renderer=renderer)  # type: ignore[arg-type]

    assert isinstance(result, UpdateResult)
    assert result.error is None
    assert result.changed is True
    assert result.ship_count == 1
    assert result.cache_invalidated == 42  # FakeRenderer 返回值
    assert renderer.invalidated_calls == [None]  # 全清


@pytest.mark.asyncio
async def test_pipeline_same_version_not_changed(
    store: Store, http_client: httpx.AsyncClient
) -> None:
    """连续两次同数据更新，第二次 changed=False 且不清缓存。"""
    kcanotify, kc3 = _make_payload()
    adapters = [
        FakeAdapter("kcanotify", kcanotify, version="v1"),
        FakeAdapter("kc3", kc3, version="kc3_v1"),
    ]
    renderer = FakeRenderer()

    first = await run_update_pipeline(store, adapters, http_client, ship_renderer=renderer)  # type: ignore[arg-type]
    assert first.changed is True

    # 第二次，相同 version
    renderer.invalidated_calls.clear()
    second = await run_update_pipeline(store, adapters, http_client, ship_renderer=renderer)  # type: ignore[arg-type]
    assert second.changed is False
    assert second.cache_invalidated == 0
    assert renderer.invalidated_calls == []  # 没调用


@pytest.mark.asyncio
async def test_pipeline_version_change_triggers_invalidation(
    store: Store, http_client: httpx.AsyncClient
) -> None:
    """版本变了 → 缓存失效被调用。"""
    kcanotify, kc3 = _make_payload()
    renderer = FakeRenderer()

    # 第一次 v1
    await run_update_pipeline(
        store,
        [FakeAdapter("kcanotify", kcanotify, "v1"), FakeAdapter("kc3", kc3, "kc3_v1")],
        http_client, ship_renderer=renderer,  # type: ignore[arg-type]
    )
    # 第二次 v2
    renderer.invalidated_calls.clear()
    result = await run_update_pipeline(
        store,
        [FakeAdapter("kcanotify", kcanotify, "v2"), FakeAdapter("kc3", kc3, "kc3_v1")],
        http_client, ship_renderer=renderer,  # type: ignore[arg-type]
    )

    assert result.changed is True
    assert result.cache_invalidated == 42
    assert renderer.invalidated_calls == [None]


@pytest.mark.asyncio
async def test_pipeline_without_renderer_skips_cache_invalidation(
    store: Store, http_client: httpx.AsyncClient
) -> None:
    """renderer=None 时正常更新，cache_invalidated=0。"""
    kcanotify, kc3 = _make_payload()
    adapters = [
        FakeAdapter("kcanotify", kcanotify, "v1"),
        FakeAdapter("kc3", kc3, "kc3_v1"),
    ]
    result = await run_update_pipeline(store, adapters, http_client, ship_renderer=None)

    assert result.error is None
    assert result.cache_invalidated == 0


# ----------------------------------------------------------------------
# 失败处理
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_all_sources_fail_returns_error(
    store: Store, http_client: httpx.AsyncClient
) -> None:
    """所有源都失败 → UpdateResult.error 非 None。"""
    result = await run_update_pipeline(
        store, [FailingAdapter()], http_client, ship_renderer=None
    )

    assert result.error is not None
    assert "no source fetched successfully" in result.error
    assert result.changed is False
    assert result.ship_count == 0


@pytest.mark.asyncio
async def test_pipeline_partial_failure_still_succeeds(
    store: Store, http_client: httpx.AsyncClient
) -> None:
    """一个源失败但另一个成功 → 正常完成，sources 中失败的标 failed。"""
    kcanotify, _ = _make_payload()
    result = await run_update_pipeline(
        store,
        [FakeAdapter("kcanotify", kcanotify, "v1"), FailingAdapter()],
        http_client,
        ship_renderer=None,
    )

    assert result.error is None
    assert result.ship_count == 1
    failed = [s for s in result.sources if s.name == "fail"]
    assert failed and failed[0].status == "failed"
    ok = [s for s in result.sources if s.name == "kcanotify"]
    assert ok and ok[0].status == "ok"


# ----------------------------------------------------------------------
# UpdateResult 结构
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_result_includes_all_source_statuses(
    store: Store, http_client: httpx.AsyncClient
) -> None:
    """sources 字段包含所有源的摘要（含 fetched_at / version / item_count）。"""
    kcanotify, kc3 = _make_payload()
    result = await run_update_pipeline(
        store,
        [FakeAdapter("kcanotify", kcanotify, "v1"), FakeAdapter("kc3", kc3, "kc3_v1")],
        http_client,
        ship_renderer=None,
    )

    names = {s.name for s in result.sources}
    assert names == {"kcanotify", "kc3"}
    for s in result.sources:
        assert s.version  # 非空
        assert s.fetched_at > 0
        assert s.status == "ok"


@pytest.mark.asyncio
async def test_pipeline_data_version_format(
    store: Store, http_client: httpx.AsyncClient
) -> None:
    """data_version 是各源 version 用 | 拼接。"""
    kcanotify, kc3 = _make_payload()
    result = await run_update_pipeline(
        store,
        [FakeAdapter("kcanotify", kcanotify, "kca_v9"), FakeAdapter("kc3", kc3, "kc3_abc")],
        http_client,
        ship_renderer=None,
    )
    # 按源名排序后拼接
    assert "kc3=kc3_abc" in result.data_version
    assert "kcanotify=kca_v9" in result.data_version
    assert "|" in result.data_version
