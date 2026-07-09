"""stype_table 单元测试。"""
from __future__ import annotations

from nonebot_plugin_kancolle.data.sources.stype_table import (
    STYPE_TABLE,
    StypeEntry,
    get_stype,
    stype_abbr,
)


def test_core_stypes_present() -> None:
    """舰种代号覆盖游戏内全部主流舰种。"""
    # 这些是绝对核心的舰种，不能缺
    must_have = [2, 3, 7, 9, 11, 13]  # DD / CL / CA / BB / CV / SS
    for sid in must_have:
        assert sid in STYPE_TABLE, f"stype {sid} missing"
        entry = STYPE_TABLE[sid]
        assert entry.jp, f"stype {sid} missing JP name"
        assert entry.cn, f"stype {sid} missing CN name"
        assert entry.en, f"stype {sid} missing EN name"
        assert entry.abbr, f"stype {sid} missing abbr"


def test_dd_is_destroyer() -> None:
    """stype 2 是驱逐舰，三语种 + 缩写都应该一致。"""
    dd = get_stype(2)
    assert dd is not None
    assert dd.jp == "駆逐艦"
    assert dd.cn == "驱逐舰"
    assert dd.en == "Destroyer"
    assert dd.abbr == "DD"


def test_stype_abbr_unknown_returns_question() -> None:
    """未知 stype id 返回 '?'。"""
    assert stype_abbr(999) == "?"


def test_get_stype_unknown_returns_none() -> None:
    assert get_stype(999) is None


def test_all_entries_have_consistent_fields() -> None:
    """所有条目的字段都不为空。"""
    for sid, entry in STYPE_TABLE.items():
        assert isinstance(entry, StypeEntry)
        assert entry.id == sid
        assert entry.jp
        assert entry.cn
        assert entry.en
        assert entry.abbr
