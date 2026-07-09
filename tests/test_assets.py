"""render/assets.py 单测：ShipArtCache + to_data_url。

用 respx mock httpx；不依赖网络。
"""
from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path

import httpx
import pytest
import respx

from nonebot_plugin_kancolle.render.assets import ShipArtCache, to_data_url

KCWIKI_URL = "https://zh.kcwiki.cn/wiki/Special:FilePath/KanMusu131HD.png"
KCWIKI_BASE = "https://zh.kcwiki.cn/wiki/Special:FilePath"


def _fake_png(size_bytes: int = 100) -> bytes:
    """构造一个最小但合法的 PNG 头（前 8 字节是 PNG magic）。"""
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * (size_bytes - 8)


@pytest.fixture()
def cache(tmp_path: Path) -> ShipArtCache:
    client = httpx.AsyncClient()
    return ShipArtCache(client, cache_root=tmp_path, ttl_days=30)


# ----------------------------------------------------------------------
# 基础流程
# ----------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_get_fetches_on_cache_miss(cache: ShipArtCache) -> None:
    png = _fake_png(120)
    respx.get(KCWIKI_URL).respond(content=png)
    result = await cache.get("131")
    assert result == png


@pytest.mark.asyncio
@respx.mock
async def test_get_caches_subsequent_calls(cache: ShipArtCache) -> None:
    route = respx.get(KCWIKI_URL).respond(content=_fake_png(100))
    first = await cache.get("131")
    second = await cache.get("131")
    assert route.call_count == 1, "second call should hit file cache"
    assert first == second


@pytest.mark.asyncio
@respx.mock
async def test_get_returns_none_on_404_and_caches_negative(cache: ShipArtCache) -> None:
    route = respx.get(KCWIKI_URL).respond(status_code=404)
    first = await cache.get("131")
    second = await cache.get("131")
    assert first is None
    assert second is None
    assert route.call_count == 1, "404 should be cached negatively"


@pytest.mark.asyncio
@respx.mock
async def test_get_returns_none_on_network_error_without_caching(cache: ShipArtCache) -> None:
    route = respx.get(KCWIKI_URL).mock(side_effect=httpx.ConnectError("simulated"))
    first = await cache.get("131")
    second = await cache.get("131")
    assert first is None
    assert second is None
    assert route.call_count == 2, "network errors should not cache"


@pytest.mark.asyncio
async def test_get_empty_wiki_id_returns_none(cache: ShipArtCache) -> None:
    assert await cache.get("") is None
    assert await cache.get(None) is None  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# 并发去重
# ----------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_concurrent_get_deduplicates(cache: ShipArtCache) -> None:
    route = respx.get(KCWIKI_URL).respond(content=_fake_png(100))
    results = await asyncio.gather(*[cache.get("131") for _ in range(5)])
    assert route.call_count == 1
    assert all(r is not None for r in results)


# ----------------------------------------------------------------------
# 缓存文件结构
# ----------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_cache_writes_png_file(tmp_path: Path, cache: ShipArtCache) -> None:
    respx.get(KCWIKI_URL).respond(content=_fake_png(80))
    await cache.get("131")
    # 文件名应是 wiki_id.png
    assert (tmp_path / "art" / "ship" / "131.png").exists()


@pytest.mark.asyncio
@respx.mock
async def test_404_writes_neg_cache_file(tmp_path: Path, cache: ShipArtCache) -> None:
    respx.get(KCWIKI_URL).respond(status_code=404)
    await cache.get("131")
    assert (tmp_path / "art" / "ship" / "131.404").exists()
    # 正向文件不应被写
    assert not (tmp_path / "art" / "ship" / "131.png").exists()


@pytest.mark.asyncio
@respx.mock
async def test_invalid_png_not_cached(cache: ShipArtCache) -> None:
    """响应非 PNG（如 HTML 错误页）应被忽略，不缓存。"""
    respx.get(KCWIKI_URL).respond(content=b"<html>not png</html>")
    result = await cache.get("131")
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_safe_filename_for_alphanumeric_wiki_id(cache: ShipArtCache) -> None:
    """wiki_id 含字母（如 031a）也应正确缓存。"""
    url = f"{KCWIKI_BASE}/KanMusu031aHD.png"
    respx.get(url).respond(content=_fake_png(100))
    result = await cache.get("031a")
    assert result is not None


# ----------------------------------------------------------------------
# invalidate
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalidate_specific_wiki_id(cache: ShipArtCache, tmp_path: Path) -> None:
    # 手工写两个文件
    (tmp_path / "art" / "ship" / "131.png").write_bytes(_fake_png(50))
    (tmp_path / "art" / "ship" / "136.png").write_bytes(_fake_png(50))
    removed = cache.invalidate("131")
    assert removed == 1
    assert not (tmp_path / "art" / "ship" / "131.png").exists()
    assert (tmp_path / "art" / "ship" / "136.png").exists()


@pytest.mark.asyncio
async def test_invalidate_all(cache: ShipArtCache, tmp_path: Path) -> None:
    (tmp_path / "art" / "ship" / "131.png").write_bytes(_fake_png(50))
    (tmp_path / "art" / "ship" / "131.404").touch()
    removed = cache.invalidate()
    assert removed == 2


# ----------------------------------------------------------------------
# to_data_url
# ----------------------------------------------------------------------

def test_to_data_url_round_trip() -> None:
    raw = b"\x89PNG\r\n\x1a\n" + b"image_data_xyz"
    url = to_data_url(raw)
    assert url.startswith("data:image/png;base64,")
    # base64 部分能解回原文
    b64_part = url.split(",", 1)[1]
    assert base64.b64decode(b64_part) == raw


def test_to_data_url_format() -> None:
    url = to_data_url(b"abc")
    assert url == "data:image/png;base64,YWJj"
