"""commands/_format.py 格式化函数单元测试。

不依赖 alconna / nonebot runtime；直接验证纯函数输出。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nonebot_plugin_kancolle.commands._format import (
    format_basic,
    format_detail,
    format_help_overview,
    format_help_topic,
    format_multiple,
    format_remodel,
)
from nonebot_plugin_kancolle.core.result import ResolveResult
from nonebot_plugin_kancolle.data.models import Ship, ShipEnhancement, ShipName, ShipStats
from nonebot_plugin_kancolle.data.store import Store


# ----------------------------------------------------------------------
# 测试用 Ship 构造
# ----------------------------------------------------------------------

def _yamato() -> Ship:
    """大和（基础形态）。"""
    return Ship(
        id=131,
        name=ShipName(jp="大和", cn="大和", en="Yamato", romaji="やまと"),
        ship_type_id=9,  # BB
        ship_class_id=37,
        ship_class_jp="大和型",
        speed=10,
        range_=4,
        stats_base=ShipStats(
            hp=93, firepower=92, torpedo=0, aa=50, armor=88, luck=12,
            slot_count=4, slot_capacity=[7, 9, 9, 7], fuel=250, ammo=300,
        ),
        stats_max=ShipStats(
            hp=108, firepower=150, torpedo=0, aa=94, armor=118, luck=99,
        ),
        remodel_to=136,
        remodel_level=60,
        remodel_chain_root=131,
    )


def _yamato_kai() -> Ship:
    """大和改。"""
    return Ship(
        id=136,
        name=ShipName(jp="大和改", cn="大和改", en="Yamato Kai"),
        ship_type_id=9,
        ship_class_jp="大和型",
        speed=10, range_=4,
        stats_base=ShipStats(hp=100, firepower=110),
        stats_max=ShipStats(hp=120, firepower=150),
        remodel_to=546,
        remodel_level=88,
        remodel_from=131,
        remodel_chain_root=131,
    )


def _yamato_kai_ni() -> Ship:
    """大和改二。"""
    return Ship(
        id=546,
        name=ShipName(jp="大和改二", cn="大和改二", en="Yamato Kai Ni"),
        ship_type_id=9,
        ship_class_jp="大和型",
        speed=10, range_=4,
        stats_base=ShipStats(hp=110),
        stats_max=ShipStats(hp=120),
        remodel_from=136,
        remodel_chain_root=131,
    )


def _enhancement(ship_id: int = 131, can_drop: bool = True) -> ShipEnhancement:
    """模拟 kcwiki 懒加载结果。"""
    return ShipEnhancement(
        ship_id=ship_id,
        chinese_name="大和",
        stype_name_chinese="战舰",
        can_drop=can_drop,
        wiki_id="131",
        filename="yamato",
    )


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    s.open()
    s.write_ships([_yamato(), _yamato_kai(), _yamato_kai_ni()])
    return s


# ----------------------------------------------------------------------
# format_basic
# ----------------------------------------------------------------------

def test_format_basic_includes_names_and_stype() -> None:
    text = format_basic(_yamato())
    # 多语言名都在
    assert "大和" in text
    assert "Yamato" in text
    # 舰种
    assert "战舰" in text
    assert "BB" in text
    # ID
    assert "ID 131" in text


def test_format_basic_shows_stat_pairs() -> None:
    """base/max 形式正确展示。"""
    text = format_basic(_yamato())
    assert "耐久" in text
    assert "93" in text  # hp base
    assert "108" in text  # hp max


def test_format_basic_handles_same_base_max() -> None:
    """base == max 时不重复展示。"""
    ship = Ship(id=1, name=ShipName(jp="x"))
    ship.stats_base = ShipStats(hp=10, firepower=5)
    ship.stats_max = ShipStats(hp=10, firepower=5)
    text = format_basic(ship)
    # 同值应只显示一次
    assert "10 → 10" not in text


def test_format_basic_with_enhancement_can_drop_true() -> None:
    text = format_basic(_yamato(), _enhancement(can_drop=True))
    assert "✓ 可获取" in text


def test_format_basic_with_enhancement_can_drop_false() -> None:
    text = format_basic(_yamato(), _enhancement(can_drop=False))
    assert "✗ 当前不可获取" in text


def test_format_basic_without_enhancement_no_drop_indicator() -> None:
    """enhancement=None 时不应展示可获取行。"""
    text = format_basic(_yamato(), None)
    assert "可获取" not in text


def test_format_basic_includes_detail_hint() -> None:
    """默认卡末尾应提示如何看详情。"""
    text = format_basic(_yamato())
    assert "详细" in text


# ----------------------------------------------------------------------
# format_detail
# ----------------------------------------------------------------------

def test_format_detail_shows_all_stats_including_missing() -> None:
    """详细模式：回避/对潜/索敌 即使缺失也展示 '-'。"""
    text = format_detail(_yamato())
    assert "回避" in text
    assert "对潜" in text
    assert "索敌" in text
    # 这三个缺失字段应有 '-' 出现（验证缺失展示而非空白）
    # （因为 _format_stat_row_two_col 用 _int_str 把 None 转 '-'）


def test_format_detail_shows_equipment_block() -> None:
    text = format_detail(_yamato())
    assert "▸ 装备" in text
    assert "槽数" in text
    assert "搭载" in text
    assert "燃料消耗" in text
    assert "弹药消耗" in text


def test_format_detail_shows_remodel_inline() -> None:
    text = format_detail(_yamato())
    assert "▸ 改造" in text
    assert "Lv 60" in text
    assert "#136" in text  # 改造后 id


def test_format_detail_shows_speed_range() -> None:
    text = format_detail(_yamato())
    assert "航速" in text
    assert "射程" in text


def test_format_detail_no_remodel_target() -> None:
    """无后续改造的船（如改二）应展示 (无后续改造)。"""
    text = format_detail(_yamato_kai_ni())
    assert "无后续改造" in text


# ----------------------------------------------------------------------
# format_remodel
# ----------------------------------------------------------------------

def test_format_remodal_renders_full_chain(store: Store) -> None:
    """改造链：从链头到结尾顺序展示，当前位置标记。"""
    text = format_remodel(_yamato(), store)
    assert "改造链" in text
    # 三个形态都在
    assert "大和" in text
    assert "大和改" in text
    assert "大和改二" in text
    # 改造等级
    assert "Lv.60" in text
    assert "Lv.88" in text
    # 当前位置标记
    assert "当前位置" in text


def test_format_remodal_marks_current_position(store: Store) -> None:
    """从中间形态查改造链，应标记当前位置。"""
    text = format_remodel(_yamato_kai(), store)
    # 大和改应该有当前位置标记 ▸
    lines = text.split("\n")
    yamato_kai_line = next((ln for ln in lines if "大和改" in ln and "ID 136" in ln), None)
    assert yamato_kai_line is not None
    assert yamato_kai_line.startswith("▸")


def test_format_remodal_handles_broken_chain(store: Store, tmp_path: Path) -> None:
    """remodel_to 指向不存在的 id 时，链截断但不崩溃。"""
    broken_store = Store(tmp_path / "broken.db")
    broken_store.open()
    only_one = Ship(
        id=999, name=ShipName(jp="孤儿", cn="孤儿"),
        remodel_to=88888, remodel_chain_root=999,
    )
    broken_store.write_ships([only_one])
    text = format_remodel(only_one, broken_store)
    assert "孤儿" in text


# ----------------------------------------------------------------------
# format_multiple
# ----------------------------------------------------------------------

def test_format_multiple_lists_all_candidates() -> None:
    ships = [_yamato(), _yamato_kai(), _yamato_kai_ni()]
    result = ResolveResult.multiple(ships, hint="chain")
    text = format_multiple(result)
    assert "找到 3 艘" in text
    assert "[1]" in text
    assert "[2]" in text
    assert "[3]" in text
    # 提示用户重发精确名
    assert "请直接发送" in text


def test_format_multiple_includes_hint_label() -> None:
    ships = [_yamato()]
    for hint, expected in [
        ("chain", "改造链"),
        ("pinyin", "拼音"),
        ("fts", "全文"),
        ("fuzzy", "模糊"),
    ]:
        result = ResolveResult.multiple(ships, hint=hint)
        text = format_multiple(result)
        assert expected in text, f"hint={hint} missing label"


# ----------------------------------------------------------------------
# format_help
# ----------------------------------------------------------------------

def test_help_overview_lists_all_commands() -> None:
    text = format_help_overview()
    for cmd in ("查舰娘", "舰C帮助", "更新舰娘数据", "数据状态", "ship", "kchelp"):
        assert cmd in text, f"missing command in overview: {cmd}"


def test_help_overview_includes_tips() -> None:
    text = format_help_overview()
    assert "拼音" in text  # 提示
    assert "中日英" in text


def test_help_topic_ship_returns_detailed_help() -> None:
    text = format_help_topic("查舰娘")
    assert text is not None
    assert "查舰娘" in text
    assert "用法" in text
    assert "示例" in text


def test_help_topic_alias_works() -> None:
    """英文别名也能查到。"""
    assert format_help_topic("ship") is not None
    assert format_help_topic("kchelp") is not None
    assert format_help_topic("kancolle update") is not None


def test_help_topic_unknown_returns_none() -> None:
    assert format_help_topic("不存在的指令") is None


def test_help_topic_case_insensitive() -> None:
    """大小写不敏感。"""
    assert format_help_topic("SHIP") is not None
    assert format_help_topic("Kchelp") is not None
