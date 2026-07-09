"""render/context.py 单测：验证模板上下文构建逻辑。

不渲染图片，只验证 dict 结构正确（jinja2 模板需要的字段都齐备）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nonebot_plugin_kancolle.data.models import Ship, ShipEnhancement, ShipName, ShipStats
from nonebot_plugin_kancolle.data.store import Store
from nonebot_plugin_kancolle.render.context import (
    STAT_SCALES,
    build_basic_context,
    build_remodel_context,
    build_stats_context,
)


# ----------------------------------------------------------------------
# 测试数据
# ----------------------------------------------------------------------

def _yamato() -> Ship:
    return Ship(
        id=131,
        name=ShipName(jp="大和", cn="大和", en="Yamato", romaji="やまと"),
        ship_type_id=9,
        ship_class_jp="大和型",
        speed=5, range_=4,
        stats_base=ShipStats(
            hp=93, firepower=96, torpedo=0, aa=50, armor=88, luck=12,
            slot_count=4, slot_capacity=[7, 7, 7, 7, 0], fuel=250, ammo=300,
        ),
        stats_max=ShipStats(
            hp=98, firepower=129, torpedo=0, aa=94, armor=108, luck=79,
        ),
        remodel_to=136, remodel_level=60, remodel_chain_root=131,
    )


def _enhancement() -> ShipEnhancement:
    return ShipEnhancement(
        ship_id=131, chinese_name="大和", stype_name_chinese="战舰",
        can_drop=True, wiki_id="131", filename="KanMusu131",
    )


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    s.open()
    s.write_ships([_yamato()])
    return s


# ----------------------------------------------------------------------
# build_basic_context
# ----------------------------------------------------------------------

def test_basic_context_has_required_fields() -> None:
    ctx = build_basic_context(_yamato(), _enhancement(), "dark")
    for field in (
        "theme", "ship_id", "display_name", "stype_cn", "stype_abbr",
        "ship_class_jp", "stats", "speed_text", "range_text",
        "can_drop", "detail_hint_name",
    ):
        assert field in ctx, f"missing field: {field}"


def test_basic_context_theme_is_passed_through() -> None:
    assert build_basic_context(_yamato(), None, "dark")["theme"] == "dark"
    assert build_basic_context(_yamato(), None, "light")["theme"] == "light"


def test_basic_context_display_name_joins_languages() -> None:
    ctx = build_basic_context(_yamato(), None, "dark")
    assert ctx["display_name"] == "大和 / 大和 / Yamato"


def test_basic_context_stype_resolved() -> None:
    ctx = build_basic_context(_yamato(), None, "dark")
    assert ctx["stype_cn"] == "战舰"
    assert ctx["stype_abbr"] == "BB"


def test_basic_context_stats_has_six_rows() -> None:
    ctx = build_basic_context(_yamato(), None, "dark")
    assert len(ctx["stats"]) == 6
    labels = [s["label"] for s in ctx["stats"]]
    assert labels == ["耐久", "火力", "雷装", "对空", "装甲", "运"]


def test_basic_context_stat_ratios_within_bounds() -> None:
    """所有 ratio 应在 [0, 1] 范围内。"""
    ctx = build_basic_context(_yamato(), None, "dark")
    for stat in ctx["stats"]:
        assert 0.0 <= stat["base_ratio"] <= 1.0, f"{stat['label']} base_ratio out of range"
        assert 0.0 <= stat["max_ratio"] <= 1.0, f"{stat['label']} max_ratio out of range"


def test_basic_context_can_drop_passthrough() -> None:
    """enhancement.can_drop 正确传到 context。"""
    assert build_basic_context(_yamato(), _enhancement(), "dark")["can_drop"] is True
    enh_false = ShipEnhancement(ship_id=131, can_drop=False)
    assert build_basic_context(_yamato(), enh_false, "dark")["can_drop"] is False


def test_basic_context_can_drop_none_without_enhancement() -> None:
    assert build_basic_context(_yamato(), None, "dark")["can_drop"] is None


def test_basic_context_speed_range_text() -> None:
    ctx = build_basic_context(_yamato(), None, "dark")
    assert ctx["speed_text"] == "慢速"  # api_soku=5
    assert ctx["range_text"] == "超长"  # api_leng=4


# ----------------------------------------------------------------------
# build_stats_context
# ----------------------------------------------------------------------

def test_stats_context_has_nine_stat_rows() -> None:
    """详细模式含 回避/对潜/索敌 三项（缺失但保留）。"""
    ctx = build_stats_context(_yamato(), None, "dark")
    assert len(ctx["stats"]) == 9
    labels = [s["label"] for s in ctx["stats"]]
    assert "回避" in labels
    assert "对潜" in labels
    assert "索敌" in labels


def test_stats_context_marks_missing_stats() -> None:
    """evasion/asw/los 在 start2 中缺失，应标记 missing=True。"""
    ctx = build_stats_context(_yamato(), None, "dark")
    by_label = {s["label"]: s for s in ctx["stats"]}
    assert by_label["回避"]["missing"] is True
    assert by_label["对潜"]["missing"] is True
    assert by_label["索敌"]["missing"] is True
    assert by_label["火力"]["missing"] is False


def test_stats_context_equipment_block_fields() -> None:
    ctx = build_stats_context(_yamato(), None, "dark")
    assert ctx["slot_count"] == 4
    assert ctx["slot_capacity"] == [7, 7, 7, 7, 0]
    assert ctx["fuel"] == 250
    assert ctx["ammo"] == 300


def test_stats_context_remodel_info() -> None:
    ctx = build_stats_context(_yamato(), None, "dark")
    assert ctx["remodel_to_id"] == 136
    assert ctx["remodel_level"] == 60
    assert ctx["remodel_from_id"] is None  # 大和本身是链头


# ----------------------------------------------------------------------
# build_remodel_context
# ----------------------------------------------------------------------

def test_remodel_context_walks_full_chain(store: Store) -> None:
    """改造链包含从链头到链尾的所有节点。"""
    ctx = build_remodel_context(_yamato(), store, "dark")
    # 注意 fixture store 里只有大和一个 ship，所以链只有 1 个节点
    # 这里主要验证 context 结构
    assert "chain" in ctx
    assert isinstance(ctx["chain"], list)
    assert len(ctx["chain"]) >= 1
    assert ctx["chain_length"] == len(ctx["chain"])


def test_remodel_context_marks_current_node(store: Store) -> None:
    ctx = build_remodel_context(_yamato(), store, "dark")
    currents = [n for n in ctx["chain"] if n["is_current"]]
    assert len(currents) == 1
    assert currents[0]["ship_id"] == 131


def test_remodel_context_node_fields(tmp_path: Path) -> None:
    """每个节点应包含 index/name/ship_id/stype_abbr/level_required/is_current。"""
    ship = Ship(
        id=1,
        name=ShipName(jp="A", cn="甲"),
        ship_type_id=2,  # DD
        remodel_to=2, remodel_chain_root=1, remodel_level=20,
    )
    ship2 = Ship(
        id=2,
        name=ShipName(jp="A改", cn="甲改"),
        ship_type_id=2,
        remodel_from=1, remodel_chain_root=1,
    )
    s = Store(tmp_path / "t.db")
    s.open()
    try:
        s.write_ships([ship, ship2])
        ctx = build_remodel_context(ship, s, "dark")
    finally:
        s.close()

    assert len(ctx["chain"]) == 2
    node1 = ctx["chain"][0]
    for field in ("index", "ship_id", "name", "stype_abbr",
                  "level_required", "is_current"):
        assert field in node1
    assert node1["name"] == "甲"
    assert node1["stype_abbr"] == "DD"
    assert node1["level_required"] is None  # 链头无前置等级

    node2 = ctx["chain"][1]
    assert node2["level_required"] == 20
    assert node2["is_current"] is False


# ----------------------------------------------------------------------
# STAT_SCALES 完整性
# ----------------------------------------------------------------------

def test_stat_scales_covers_all_basic_stats() -> None:
    """STAT_SCALES 必须含所有可能出现的 stat key，否则 ratio 计算会失败。"""
    required = {"hp", "firepower", "torpedo", "aa", "armor",
                "evasion", "asw", "los", "luck"}
    assert required <= set(STAT_SCALES.keys())
    for k, v in STAT_SCALES.items():
        assert v > 0, f"{k} scale must be positive"
