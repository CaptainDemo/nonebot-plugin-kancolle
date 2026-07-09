"""commands/_format.py 中 P6 新增的格式化函数单测。

format_update_result / format_data_status 都是纯函数。
"""
from __future__ import annotations

from dataclasses import dataclass

from nonebot_plugin_kancolle.commands._format import (
    format_data_status,
    format_update_result,
    format_help_topic,
)


@dataclass
class _FakeUpdateResult:
    """模拟 update.pipeline.UpdateResult。"""
    data_version: str
    changed: bool
    ship_count: int
    cache_invalidated: int
    sources: list
    error: str | None = None


@dataclass
class _FakeSource:
    name: str
    version: str
    status: str
    fetched_at: int
    item_count: int
    error_msg: str = ""


# ----------------------------------------------------------------------
# format_update_result
# ----------------------------------------------------------------------

def test_update_result_error_branch() -> None:
    """error 非 None 时显示错误信息。"""
    r = _FakeUpdateResult(
        data_version="", changed=False, ship_count=0,
        cache_invalidated=0, sources=[],
        error="network timeout",
    )
    text = format_update_result(r)
    assert "✗ 更新失败" in text
    assert "network timeout" in text


def test_update_result_changed_with_cache_invalidation() -> None:
    r = _FakeUpdateResult(
        data_version="v1|v2", changed=True,
        ship_count=1681, cache_invalidated=350,
        sources=[], error=None,
    )
    text = format_update_result(r)
    assert "✓ 更新完成" in text
    assert "v1|v2" in text
    assert "1681" in text
    assert "350" in text
    assert "缓存失效" in text


def test_update_result_changed_without_cache() -> None:
    """changed=True 但 cache_invalidated=0（如 renderer 不可用）。"""
    r = _FakeUpdateResult(
        data_version="v1", changed=True,
        ship_count=100, cache_invalidated=0,
        sources=[], error=None,
    )
    text = format_update_result(r)
    assert "✓ 更新完成" in text
    assert "缓存失效" not in text


def test_update_result_unchanged() -> None:
    r = _FakeUpdateResult(
        data_version="v1", changed=False,
        ship_count=1681, cache_invalidated=0,
        sources=[], error=None,
    )
    text = format_update_result(r)
    assert "✓ 数据已是最新" in text
    assert "1681" in text


# ----------------------------------------------------------------------
# format_data_status
# ----------------------------------------------------------------------

def test_data_status_empty_when_no_sources() -> None:
    """尚未拉取任何数据时给出引导提示。"""
    text = format_data_status("", 0, [])
    assert "数据状态" in text
    assert "(无记录" in text or "尚未拉取" in text
    assert "更新舰娘数据" in text  # 引导用户去拉


def test_data_status_lists_each_source() -> None:
    sources = [
        _FakeSource(
            name="kcanotify", version="6.3.1.0", status="ok",
            fetched_at=1731600000, item_count=1681,
        ),
        _FakeSource(
            name="kc3", version="abc123", status="ok",
            fetched_at=1731600000, item_count=0,
        ),
    ]
    text = format_data_status("kc3=abc123|kcanotify=6.3.1.0", 1681, sources)
    assert "kcanotify" in text
    assert "kc3" in text
    assert "6.3.1.0" in text
    assert "abc123" in text
    assert "1681" in text
    assert "✓" in text  # ok 状态图标
    # 时间格式化
    assert "2024-" in text  # 1731600000 是 2024-11-15


def test_data_status_shows_error_msg_for_failed() -> None:
    sources = [
        _FakeSource(
            name="kcanotify", version="", status="failed",
            fetched_at=1731600000, item_count=0,
            error_msg="connection refused",
        ),
    ]
    text = format_data_status("", 0, sources)
    assert "✗" in text  # failed 状态图标
    assert "connection refused" in text


def test_data_status_uses_correct_icon_for_each_status() -> None:
    sources = [
        _FakeSource("a", "v1", "ok", 0, 0),
        _FakeSource("b", "v1", "failed", 0, 0),
        _FakeSource("c", "v1", "stale", 0, 0),
        _FakeSource("d", "v1", "pending", 0, 0),
    ]
    text = format_data_status("v", 0, sources)
    # 4 种状态对应 4 种图标
    assert "✓ ok" in text
    assert "✗ failed" in text
    assert "⚠ stale" in text
    assert "○ pending" in text


def test_data_status_includes_last_update_time() -> None:
    """多源时取最大 fetched_at 作为"最后更新"。"""
    sources = [
        _FakeSource("a", "v1", "ok", 1731600000, 0),  # 2024-11-15
        _FakeSource("b", "v1", "ok", 1731700000, 0),  # 更晚
    ]
    text = format_data_status("v", 0, sources)
    assert "最后更新" in text
    # 取最大值
    assert "2024-11" in text


def test_data_status_no_fetched_at_skips_last_update() -> None:
    """所有源 fetched_at=0 时不显示"最后更新"行。"""
    sources = [_FakeSource("a", "v1", "pending", 0, 0)]
    text = format_data_status("v", 0, sources)
    assert "最后更新" not in text


# ----------------------------------------------------------------------
# help_topic 覆盖更新/状态
# ----------------------------------------------------------------------

def test_help_topic_update_works() -> None:
    text = format_help_topic("更新舰娘数据")
    assert text is not None
    assert "更新舰娘数据" in text
    assert "SUPERUSER" in text


def test_help_topic_status_works() -> None:
    text = format_help_topic("数据状态")
    assert text is not None
    assert "数据状态" in text


def test_help_topic_kancolle_update_alias() -> None:
    assert format_help_topic("kancolle update") is not None


def test_help_topic_kancolle_status_alias() -> None:
    assert format_help_topic("kancolle status") is not None
