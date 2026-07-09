"""render/cache.py 单测：ImageCache 的 get/set/invalidate。"""
from __future__ import annotations

from pathlib import Path

import pytest

from nonebot_plugin_kancolle.render.cache import ImageCache


@pytest.fixture()
def cache(tmp_path: Path) -> ImageCache:
    return ImageCache(tmp_path / "render_cache")


# ----------------------------------------------------------------------
# 基础读写
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_uncached_returns_none(cache: ImageCache) -> None:
    assert await cache.get("missing_key") is None


@pytest.mark.asyncio
async def test_set_then_get(cache: ImageCache) -> None:
    await cache.set("ship_basic_131_dark_v1", b"\x89PNG\r\n\x1a\n" + b"fake_png_data")
    got = await cache.get("ship_basic_131_dark_v1")
    assert got is not None
    assert got.startswith(b"\x89PNG")
    assert b"fake_png_data" in got


@pytest.mark.asyncio
async def test_set_overwrites_existing(cache: ImageCache) -> None:
    await cache.set("k", b"old")
    await cache.set("k", b"new long content")
    got = await cache.get("k")
    assert got == b"new long content"


@pytest.mark.asyncio
async def test_set_creates_parent_dir(tmp_path: Path) -> None:
    """ImageCache 构造时应自动创建嵌套目录。"""
    nested = tmp_path / "a" / "b" / "c"
    cache = ImageCache(nested)
    await cache.set("x", b"y")
    assert (nested / "x.png").exists()


# ----------------------------------------------------------------------
# 失效
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalidate_specific_ship(cache: ImageCache) -> None:
    """按 ship_id 失效，只删该船的缓存。"""
    # 模拟 3 张图：2 张属于 ship 131（basic + stats），1 张属于 ship 136
    await cache.set("ship_basic_131_dark_v1", b"a")
    await cache.set("ship_stats_131_dark_v1", b"b")
    await cache.set("ship_basic_136_dark_v1", b"c")

    removed = cache.invalidate(ship_id=131)
    assert removed == 2
    assert await cache.get("ship_basic_131_dark_v1") is None
    assert await cache.get("ship_stats_131_dark_v1") is None
    assert await cache.get("ship_basic_136_dark_v1") is not None  # 保留


@pytest.mark.asyncio
async def test_invalidate_all(cache: ImageCache) -> None:
    await cache.set("a_1_dark_v1", b"x")
    await cache.set("b_2_light_v2", b"y")
    removed = cache.invalidate()
    assert removed == 2
    assert await cache.get("a_1_dark_v1") is None
    assert await cache.get("b_2_light_v2") is None


# ----------------------------------------------------------------------
# 统计
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stats_reports_count_and_bytes(cache: ImageCache) -> None:
    await cache.set("k1", b"12345")
    await cache.set("k2", b"abc")
    s = cache.stats()
    assert s["count"] == 2
    assert s["bytes"] == 8


@pytest.mark.asyncio
async def test_stats_empty(cache: ImageCache) -> None:
    s = cache.stats()
    assert s["count"] == 0
    assert s["bytes"] == 0
