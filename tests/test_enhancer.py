"""KcwikiEnhancer 单测。

用 respx mock httpx；不依赖网络。
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import pytest
import respx

from nonebot_plugin_kancolle.data.enhancer import KcwikiEnhancer
from nonebot_plugin_kancolle.data.models import ShipEnhancement
from nonebot_plugin_kancolle.data.store import Store


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    s.open()
    return s


def _sample_payload(ship_id: int = 1) -> dict:
    """模拟 kcwiki /ship/{id} 响应。"""
    return {
        "id": ship_id,
        "name": "睦月",
        "sort_no": 31,
        "stype": 2,
        "after_ship_id": "254",
        "filename": "snohitatusbk",
        "wiki_id": "031",
        "chinese_name": "睦月",
        "stype_name": "駆逐艦",
        "stype_name_chinese": "驱逐舰",
        "can_drop": True,
    }


# ----------------------------------------------------------------------
# 基础流程
# ----------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_get_fetches_on_cache_miss(store: Store) -> None:
    respx.get("https://api.kcwiki.moe/ship/1").respond(json=_sample_payload(1))

    async with httpx.AsyncClient() as client:
        enh = KcwikiEnhancer(client, store, ttl_days=7)
        result = await enh.get(1)

    assert result is not None
    assert result.ship_id == 1
    assert result.chinese_name == "睦月"
    assert result.can_drop is True
    assert result.wiki_id == "031"
    assert result.filename == "snohitatusbk"
    assert result.stype_name_chinese == "驱逐舰"


@pytest.mark.asyncio
@respx.mock
async def test_get_caches_subsequent_calls(store: Store) -> None:
    """第二次调用不应再发请求（缓存命中）。"""
    route = respx.get("https://api.kcwiki.moe/ship/1").respond(json=_sample_payload(1))

    async with httpx.AsyncClient() as client:
        enh = KcwikiEnhancer(client, store, ttl_days=7)
        first = await enh.get(1)
        second = await enh.get(2 if False else 1)  # 同 id

    assert route.call_count == 1, "second call should hit cache, not network"
    assert first is not None
    assert second is not None
    assert first.ship_id == second.ship_id


@pytest.mark.asyncio
@respx.mock
async def test_get_returns_none_on_404_caches_negative(store: Store) -> None:
    """404 时返回 None，缓存为 not_found；二次调用也不发请求。"""
    route = respx.get("https://api.kcwiki.moe/ship/99999").respond(status_code=404)

    async with httpx.AsyncClient() as client:
        enh = KcwikiEnhancer(client, store, not_found_ttl_hours=1)
        first = await enh.get(99999)
        second = await enh.get(99999)

    assert first is None
    assert second is None
    assert route.call_count == 1, "404 should be cached as not_found"


@pytest.mark.asyncio
@respx.mock
async def test_get_returns_none_on_network_error_without_caching(store: Store) -> None:
    """网络错误不缓存，下次仍会重试。"""
    route = respx.get("https://api.kcwiki.moe/ship/1").mock(
        side_effect=httpx.ConnectError("simulated")
    )

    async with httpx.AsyncClient() as client:
        enh = KcwikiEnhancer(client, store)
        first = await enh.get(1)
        second = await enh.get(1)

    assert first is None
    assert second is None
    # 网络错误未缓存，所以两次都发了请求
    assert route.call_count == 2


# ----------------------------------------------------------------------
# 并发去重
# ----------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_concurrent_get_deduplicates(store: Store) -> None:
    """同一 ship_id 的并发请求只发一个 HTTP。"""
    # 用一个小延迟让并发真正发生
    route = respx.get("https://api.kcwiki.moe/ship/1").respond(json=_sample_payload(1))

    async with httpx.AsyncClient() as client:
        enh = KcwikiEnhancer(client, store)
        # 同时发起 5 个请求
        results = await asyncio.gather(*[enh.get(1) for _ in range(5)])

    assert route.call_count == 1, "concurrent requests for same ship_id deduplicated"
    assert all(r is not None for r in results)
    assert all(r.ship_id == 1 for r in results if r is not None)


# ----------------------------------------------------------------------
# TTL 过期
# ----------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_cache_expires_after_ttl(store: Store, monkeypatch) -> None:
    """TTL 到期后，下次 get 应重新拉取。"""
    route = respx.get("https://api.kcwiki.moe/ship/1").respond(json=_sample_payload(1))

    async with httpx.AsyncClient() as client:
        enh = KcwikiEnhancer(client, store, ttl_days=1)  # 1 天 TTL
        await enh.get(1)

        # 模拟时间前进了 2 天
        import nonebot_plugin_kancolle.data.enhancer as enh_mod
        real_time = enh_mod.time.time
        monkeypatch.setattr(enh_mod.time, "time", lambda: real_time() + 86400 * 2)

        # 重新拉取（旧的 Lock 字典可能是 stale 的，新 enhancer 模拟重启）
        enh_fresh = KcwikiEnhancer(client, store, ttl_days=1)
        await enh_fresh.get(1)

    assert route.call_count == 2, "expired cache should trigger re-fetch"


# ----------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------

def test_parse_kcwiki_payload_full() -> None:
    from nonebot_plugin_kancolle.data.enhancer import _parse_kcwiki_payload

    result = _parse_kcwiki_payload(1, _sample_payload(1))
    assert result is not None
    assert result.ship_id == 1
    assert result.chinese_name == "睦月"
    assert result.can_drop is True


def test_parse_kcwiki_payload_id_mismatch_returns_none() -> None:
    from nonebot_plugin_kancolle.data.enhancer import _parse_kcwiki_payload

    payload = _sample_payload(99)  # 故意改 id
    result = _parse_kcwiki_payload(1, payload)
    assert result is None


def test_parse_kcwiki_payload_missing_fields_returns_partial() -> None:
    """kcwiki 偶尔会缺字段，应优雅降级（其他字段保留）。"""
    from nonebot_plugin_kancolle.data.enhancer import _parse_kcwiki_payload

    payload = {"id": 1, "name": "test"}  # 缺很多字段
    result = _parse_kcwiki_payload(1, payload)
    assert result is not None
    assert result.ship_id == 1
    assert result.can_drop is None
    assert result.wiki_id is None


def test_parse_kcwiki_payload_non_dict_returns_none() -> None:
    from nonebot_plugin_kancolle.data.enhancer import _parse_kcwiki_payload

    assert _parse_kcwiki_payload(1, ["not", "a", "dict"]) is None
    assert _parse_kcwiki_payload(1, "string") is None
