"""装备文本格式化函数测试（P7 + P7.1）。"""
from __future__ import annotations

from nonebot_plugin_kancolle.commands._format import (
    format_equipment_basic,
    format_equipment_detail,
    format_equipment_multiple,
    format_help_topic,
)
from nonebot_plugin_kancolle.core.result import EquipmentResolveResult
from nonebot_plugin_kancolle.data.models import (
    Equipment, EquipmentName, EquipmentStats,
    ImprovementData, ImprovementEntry, ImprovementMaterial,
    ImprovementRecipe, ImprovementUpgrade,
)


def _make_gun() -> Equipment:
    return Equipment(
        id=1,
        name=EquipmentName(jp="12cm単装砲", cn="12cm单装炮", en="12cm Single Gun"),
        type_icon_id=1,
        type_id=1,
        rarity=2,
        range_=1,
        stats=EquipmentStats(firepower=1, aa=2, evasion=1, accuracy=1),
        broken=[0, 1, 1, 0],
    )


def _make_plane() -> Equipment:
    return Equipment(
        id=25,
        name=EquipmentName(jp="零式水上偵察機", cn="零式水上侦察机", en="Type 0 Recon"),
        type_id=10,
        rarity=1,
        range_=0,
        stats=EquipmentStats(aa=1, asw=2, los=5, accuracy=1),
        distance=10,
        cost=4,
        broken=[0, 0, 0, 4],
    )


def _make_improvement() -> ImprovementData:
    return ImprovementData(
        equip_id=1,
        entries=[
            ImprovementEntry(
                upgrade=ImprovementUpgrade(level=0, target_id=228, target_name="升级目标"),
                recipes=[
                    ImprovementRecipe(day=[True] * 7, secretary_names=["凤翔", "赤城"]),
                ],
                materials=[
                    ImprovementMaterial(
                        development=[1, 2], improvement_res=[1, 1],
                        item_id=35, item_name="消耗装备", item_count=1,
                    ),
                ],
                fuel=70, ammo=70, steel=70, bauxite=70,
            )
        ],
    )


def _type_entry(type_id: int, cn: str) -> dict[str, object]:
    return {"type_id": type_id, "name_jp": cn, "name_cn": cn, "name_en": cn}


# ----------------------------------------------------------------------
# basic（P7.1 合并版）
# ----------------------------------------------------------------------

def test_format_basic_includes_names_and_type() -> None:
    e = _make_gun()
    text = format_equipment_basic(e, _type_entry(1, "小口径主炮"))
    assert "12cm単装砲" in text
    assert "12cm单装炮" in text
    assert "小口径主炮" in text
    assert "★2" in text
    assert "ID 1" in text


def test_format_basic_includes_all_stats() -> None:
    """P7.1 合并后 basic 含完整 10 项数值。"""
    e = _make_gun()
    text = format_equipment_basic(e, None)
    for label in ("火力", "雷装", "对空", "装甲", "对潜", "索敌", "回避", "命中", "运", "爆装"):
        assert label in text


def test_format_basic_includes_extras_for_plane() -> None:
    """P7.1 合并后 basic 也含飞机/废弃信息。"""
    e = _make_plane()
    text = format_equipment_basic(e, _type_entry(10, "水上侦察机"))
    assert "半径" in text
    assert "配置成本" in text
    assert "废弃返还" in text
    assert "铝" in text


def test_format_basic_shows_detail_hint() -> None:
    e = _make_gun()
    text = format_equipment_basic(e, None)
    assert "查装备" in text
    assert "详细" in text


def test_format_basic_no_extras_placeholder() -> None:
    """普通装备无 distance/cost/broken 时仍正常显示。"""
    e = Equipment(id=99, name=EquipmentName(jp="x"), stats=EquipmentStats(firepower=1))
    text = format_equipment_basic(e, None)
    assert "数值" in text


# ----------------------------------------------------------------------
# detail（P7.1 含改修）
# ----------------------------------------------------------------------

def test_format_detail_without_improvement_shows_warning() -> None:
    """无改修数据时 detail 显示提示。"""
    e = _make_gun()
    text = format_equipment_detail(e, None, None)
    assert "暂无改修数据" in text


def test_format_detail_with_improvement() -> None:
    """有改修数据时 detail 含改修信息。"""
    e = _make_gun()
    text = format_equipment_detail(e, None, _make_improvement())
    assert "改修数据" in text
    assert "凤翔" in text
    assert "升级目标" in text
    assert "开发" in text


# ----------------------------------------------------------------------
# multiple 与 help
# ----------------------------------------------------------------------

def test_format_multiple_lists_all_candidates() -> None:
    e1 = _make_gun()
    e2 = _make_plane()
    result = EquipmentResolveResult.multiple([e1, e2], hint="fts", message="测试")
    text = format_equipment_multiple(result)
    assert "12cm" in text
    assert "零式" in text
    assert "请直接发送具体装备名" in text


def test_format_multiple_includes_hint_label() -> None:
    e1 = _make_gun()
    result = EquipmentResolveResult.multiple([e1], hint="pinyin", message="")
    text = format_equipment_multiple(result)
    assert "拼音匹配" in text


def test_format_help_topic_equipment_returns_help() -> None:
    """查装备 / equip 都返回装备帮助。"""
    text1 = format_help_topic("查装备")
    text2 = format_help_topic("equip")
    assert text1 is not None
    assert text2 is not None
    assert "查装备" in text1
    # P7.1 帮助应含改修说明
    assert "改修" in text1
