"""装备渲染上下文构建测试（P7 + P7.1）。"""
from __future__ import annotations

from nonebot_plugin_kancolle.data.models import (
    Equipment, EquipmentName, EquipmentStats,
    ImprovementData, ImprovementEntry, ImprovementMaterial,
    ImprovementRecipe, ImprovementUpgrade,
)
from nonebot_plugin_kancolle.render.context import (
    EQUIP_STAT_SCALES,
    build_equipment_basic_context,
    build_improvement_context,
)


def _make_equipment() -> Equipment:
    return Equipment(
        id=25,
        name=EquipmentName(jp="零式水上偵察機", cn="零式水上侦察机", en="Type 0 Recon"),
        type_icon_id=10,
        type_id=10,
        rarity=3,
        range_=1,
        stats=EquipmentStats(firepower=1, aa=2, asw=5, los=7, evasion=2, accuracy=1),
        distance=10,
        cost=4,
        broken=[0, 0, 0, 4],
    )


def _type_entry() -> dict[str, object]:
    return {"type_id": 10, "name_jp": "水上偵察機", "name_cn": "水上侦察机", "name_en": "Seaplane"}


# ----------------------------------------------------------------------
# 基础卡 context（P7.1 合并版）
# ----------------------------------------------------------------------

def test_basic_context_has_required_fields() -> None:
    ctx = build_equipment_basic_context(_make_equipment(), _type_entry(), "dark")
    for key in (
        "theme", "equip_id", "display_name", "type_cn", "rarity",
        "rarity_label", "core_stats", "full_stats", "range_text",
        "detail_hint_name", "name_jp", "name_cn", "name_en",
        "distance", "cost", "broken_pairs",
    ):
        assert key in ctx, f"缺少字段 {key}"
    assert ctx["equip_id"] == 25
    assert ctx["type_cn"] == "水上侦察机"
    assert ctx["rarity"] == 3


def test_basic_context_core_stats_has_value_ratio() -> None:
    """核心 6 数值含 value_ratio 字段（用于条形图）。"""
    ctx = build_equipment_basic_context(_make_equipment(), None, "dark")
    assert len(ctx["core_stats"]) == 6  # 火力/雷装/对空/装甲/对潜/索敌
    for row in ctx["core_stats"]:
        for key in ("label", "value", "value_ratio", "missing"):
            assert key in row
        assert isinstance(row["value_ratio"], float)
        assert row["value_ratio"] >= 0


def test_basic_context_full_stats_has_10_rows() -> None:
    """完整数值表含 10 行。"""
    ctx = build_equipment_basic_context(_make_equipment(), None, "dark")
    assert len(ctx["full_stats"]) == 10


def test_basic_context_includes_distance_cost_broken() -> None:
    """合并后 basic context 含飞机/废弃字段（来自 stats.html）。"""
    ctx = build_equipment_basic_context(_make_equipment(), _type_entry(), "dark")
    assert ctx["distance"] == 10
    assert ctx["cost"] == 4
    assert len(ctx["broken_pairs"]) == 4
    labels = [bp["label"] for bp in ctx["broken_pairs"]]
    assert labels == ["燃料", "弹药", "钢材", "铝"]


def test_basic_context_falls_back_when_type_none() -> None:
    ctx = build_equipment_basic_context(_make_equipment(), None, "dark")
    assert ctx["type_cn"] == "未知"
    assert ctx["type_jp"] is None


def test_basic_context_handles_no_broken() -> None:
    """无 broken 数据的装备也能正常构造 context。"""
    e = Equipment(id=1, name=EquipmentName(jp="x"), stats=EquipmentStats(firepower=1))
    ctx = build_equipment_basic_context(e, None, "dark")
    assert ctx["broken_pairs"] == []
    assert ctx["distance"] is None
    assert ctx["cost"] is None


def test_basic_context_rarity_label_correct() -> None:
    e = Equipment(id=1, name=EquipmentName(jp="x"), rarity=5)
    ctx = build_equipment_basic_context(e, None, "dark")
    assert ctx["rarity_label"] == "SSR"


def test_equip_stat_scales_covers_all_stats_fields() -> None:
    """STAT_SCALES 应覆盖所有 EquipmentStats 字段。"""
    stat_attrs = set(EquipmentStats.model_fields.keys())
    assert stat_attrs <= set(EQUIP_STAT_SCALES.keys())


# ----------------------------------------------------------------------
# 改修卡 context（P7.1）
# ----------------------------------------------------------------------

def _make_improvement() -> ImprovementData:
    return ImprovementData(
        equip_id=87,
        entries=[
            ImprovementEntry(
                upgrade=ImprovementUpgrade(level=0, target_id=228, target_name="20.3cm(3号)連装砲"),
                recipes=[
                    ImprovementRecipe(
                        day=[True, True, True, True, True, True, True],
                        secretary_names=["鳥海改二", "摩耶改二"],
                    ),
                ],
                materials=[
                    ImprovementMaterial(
                        development=[1, 2],
                        improvement_res=[1, 1],
                        item_id=35, item_name="7.7mm機銃", item_count=1,
                    ),
                    ImprovementMaterial(
                        development=[2, 3],
                        improvement_res=[1, 2],
                        item_id=35, item_name="7.7mm機銃", item_count=2,
                    ),
                ],
                fuel=70, ammo=70, steel=70, bauxite=70,
            )
        ],
    )


def test_improvement_context_has_required_fields() -> None:
    ctx = build_improvement_context(_make_equipment(), _make_improvement(), "dark")
    for key in ("theme", "equip_id", "display_name", "day_labels", "sections", "bonus_placeholder"):
        assert key in ctx, f"缺少字段 {key}"


def test_improvement_context_sections_structure() -> None:
    ctx = build_improvement_context(_make_equipment(), _make_improvement(), "dark")
    assert len(ctx["sections"]) == 1
    section = ctx["sections"][0]
    # 关键字段
    for key in (
        "index", "has_multiple", "base_pills", "stage_labels",
        "dev_row", "imp_row", "item_row", "recipe_groups", "upgrade_text",
    ):
        assert key in section, f"section 缺少字段 {key}"
    # 基础消耗 pill
    pill_labels = [p["label"] for p in section["base_pills"]]
    assert pill_labels == ["燃料", "弹药", "钢材", "铝"]
    # 阶段标签：2 段 material → 2 个阶段标签
    assert section["stage_labels"] == ["★0-5", "★6-9"]
    # dev_row 长度匹配 material 数
    assert len(section["dev_row"]) == 2
    assert section["dev_row"][0] == "1-2"
    assert section["imp_row"][0] == "1-1"
    # 升级链
    assert section["upgrade_text"] is not None
    assert "20.3cm(3号)連装砲" in section["upgrade_text"]
    # recipe_groups 含秘书舰
    assert len(section["recipe_groups"]) == 1
    assert "鳥海改二" in section["recipe_groups"][0]["secretaries"]
    assert section["recipe_groups"][0]["day"] == [True] * 7


def test_improvement_context_3_stage_with_upgrade() -> None:
    """3 段 material 时 stage_labels 应含升级阶段。"""
    imp = ImprovementData(
        equip_id=87,
        entries=[
            ImprovementEntry(
                materials=[
                    ImprovementMaterial(development=[1, 1], improvement_res=[1, 1]),
                    ImprovementMaterial(development=[2, 2], improvement_res=[1, 2]),
                    ImprovementMaterial(development=[3, 3], improvement_res=[2, 3]),
                ],
                recipes=[ImprovementRecipe(day=[True] * 7, secretary_names=["x"])],
            )
        ],
    )
    ctx = build_improvement_context(_make_equipment(), imp, "dark")
    section = ctx["sections"][0]
    assert section["stage_labels"] == ["★0-5", "★6-9", "★max→升级"]
