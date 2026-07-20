"""ImprovementEnhancer 集成测试（P7.1）。

用 respx mock GitHub API，验证：
- 首次查询触发全量拉取
- 后续查询走 SQLite 缓存
- 全局锁避免并发拉取
- 数据版本指纹写入 meta
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
import respx

from nonebot_plugin_kancolle.data.improvement_enhancer import (
    IMPROVE_URL, ImprovementEnhancer,
)
from nonebot_plugin_kancolle.data.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "improve_data_sample.json"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    s.open()
    return s


@pytest.fixture()
def payload_bytes() -> bytes:
    return FIXTURE.read_bytes()


@pytest.mark.asyncio
async def test_get_triggers_fetch_on_first_call(
    store: Store, payload_bytes: bytes
) -> None:
    """首次查询触发拉取，写入 equipment_improvements 表。"""
    async with httpx.AsyncClient() as client:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(IMPROVE_URL).mock(
                httpx.Response(200, content=payload_bytes)
            )
            # GitHub commit API mock
            mock.get(
                "https://api.github.com/repos/kcwikizh/kcwiki-improvement-data/commits/gh-pages"
            ).mock(httpx.Response(200, json={"sha": "abc123"}))

            enhancer = ImprovementEnhancer(client, store)
            data = await enhancer.get(87)

    assert data is not None
    assert data.equip_id == 87
    assert len(data.entries) == 1
    # 版本指纹写入 meta
    assert store.get_meta("improvement_version") == "abc123"


@pytest.mark.asyncio
async def test_second_get_uses_cache_no_network(
    store: Store, payload_bytes: bytes
) -> None:
    """第二次查询走 SQLite，不应再次拉取（respx 限定 called_once）。"""
    async with httpx.AsyncClient() as client:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(IMPROVE_URL).mock(
                httpx.Response(200, content=payload_bytes)
            )
            mock.get(
                "https://api.github.com/repos/kcwikizh/kcwiki-improvement-data/commits/gh-pages"
            ).mock(httpx.Response(200, json={"sha": "v1"}))

            enhancer = ImprovementEnhancer(client, store)
            await enhancer.get(25)

            # 重置路由调用计数
            route.calls.clear()
            # 第二次查询：不应再请求
            data = await enhancer.get(25)

    assert data is not None
    assert data.equip_id == 25
    assert route.call_count == 0


@pytest.mark.asyncio
async def test_get_unknown_equip_returns_none(
    store: Store, payload_bytes: bytes
) -> None:
    """improve_data.json 中无此装备 id → 返回 None。"""
    async with httpx.AsyncClient() as client:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(IMPROVE_URL).mock(
                httpx.Response(200, content=payload_bytes)
            )
            mock.get(
                "https://api.github.com/repos/kcwikizh/kcwiki-improvement-data/commits/gh-pages"
            ).mock(httpx.Response(200, json={"sha": "v1"}))

            enhancer = ImprovementEnhancer(client, store)
            data = await enhancer.get(99999)  # 不存在

    assert data is None


@pytest.mark.asyncio
async def test_concurrent_get_deduplicates(
    store: Store, payload_bytes: bytes
) -> None:
    """并发 5 个装备查询：全局锁保证只拉一次。"""
    async with httpx.AsyncClient() as client:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(IMPROVE_URL).mock(
                httpx.Response(200, content=payload_bytes)
            )
            mock.get(
                "https://api.github.com/repos/kcwikizh/kcwiki-improvement-data/commits/gh-pages"
            ).mock(httpx.Response(200, json={"sha": "v1"}))

            enhancer = ImprovementEnhancer(client, store)
            results = await asyncio.gather(*[
                enhancer.get(eid) for eid in (87, 25, 285, 999, 1000)
            ])

    # 5 个装备都有数据（999 也有，因为 fixture 里有）
    assert all(r is not None for r in results)
    # 全局锁：只拉取一次
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_network_failure_returns_none(
    store: Store,
) -> None:
    """网络失败时返回 None，不缓存（下次会重试）。"""
    async with httpx.AsyncClient() as client:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(IMPROVE_URL).mock(httpx.Response(500))
            mock.get(
                "https://api.github.com/repos/kcwikizh/kcwiki-improvement-data/commits/gh-pages"
            ).mock(httpx.Response(500))

            enhancer = ImprovementEnhancer(client, store)
            data = await enhancer.get(87)

    assert data is None
    # 网络失败不入库
    assert store.get_improvement(87) is None
