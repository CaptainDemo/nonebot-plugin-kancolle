"""装备模型序列化往返测试（P7 + P7.1）。"""
from __future__ import annotations

from nonebot_plugin_kancolle.data.models import (
    Equipment, EquipmentName, EquipmentStats,
    ImprovementData, ImprovementEntry, ImprovementMaterial,
    ImprovementRecipe, ImprovementUpgrade,
)


def test_equipment_stats_defaults_all_none() -> None:
    """EquipmentStats 所有字段默认 None。"""
    stats = EquipmentStats()
    for attr in (
        "firepower", "torpedo", "aa", "armor", "asw", "los",
        "evasion", "accuracy", "luck", "bombing",
    ):
        assert getattr(stats, attr) is None


def test_equipment_stats_round_trip_json() -> None:
    """JSON 序列化往返保持字段值。"""
    stats = EquipmentStats(firepower=10, torpedo=5, aa=8, bombing=20)
    j = stats.model_dump_json()
    restored = EquipmentStats.model_validate_json(j)
    assert restored.firepower == 10
    assert restored.torpedo == 5
    assert restored.aa == 8
    assert restored.bombing == 20
    assert restored.armor is None


def test_equipment_minimal_construction() -> None:
    """仅 id 必填，其他全 Optional。"""
    e = Equipment(id=1)
    assert e.id == 1
    assert e.name.jp is None
    assert e.aliases == []
    assert e.rarity is None
    assert e.broken is None
    assert e.provenance == {}


def test_equipment_full_construction() -> None:
    """完整字段构造。"""
    e = Equipment(
        id=25,
        name=EquipmentName(jp="零式水上偵察機", cn="零式水上侦察机", en="Type 0 Recon"),
        type_icon_id=10,
        type_id=10,
        rarity=3,
        range_=1,
        stats=EquipmentStats(asw=5, los=7),
        distance=10,
        cost=4,
        broken=[0, 1, 2, 6],
    )
    assert e.name.cn == "零式水上侦察机"
    assert e.type_id == 10
    assert e.rarity == 3
    assert e.stats.asw == 5
    assert e.broken == [0, 1, 2, 6]


def test_equipment_round_trip_json() -> None:
    """完整 Equipment JSON 往返。"""
    e = Equipment(
        id=100,
        name=EquipmentName(jp="x", cn="测试装备"),
        rarity=2,
        stats=EquipmentStats(firepower=15),
        broken=[1, 2, 3, 4],
        provenance={"rarity": {"source": "kcanotify", "version": "v1", "fetched_at": 0}},
    )
    j = e.model_dump_json()
    restored = Equipment.model_validate_json(j)
    assert restored.id == 100
    assert restored.name.cn == "测试装备"
    assert restored.rarity == 2
    assert restored.stats.firepower == 15
    assert restored.broken == [1, 2, 3, 4]
    assert "rarity" in restored.provenance


# ----------------------------------------------------------------------
# Improvement 模型（P7.1）
# ----------------------------------------------------------------------

def test_improvement_recipe_normalize_day_static() -> None:
    """normalize_day 工具方法处理异常长度。"""
    assert ImprovementRecipe.normalize_day(None) == [False] * 7
    assert ImprovementRecipe.normalize_day([]) == [False] * 7
    assert ImprovementRecipe.normalize_day([True]) == [True] + [False] * 6
    assert ImprovementRecipe.normalize_day([True] * 35) == [True] * 7


def test_improvement_recipe_basic_construction() -> None:
    r = ImprovementRecipe(day=[True] * 7, secretary_names=["凤翔"])
    assert r.day == [True] * 7
    assert r.secretary_names == ["凤翔"]


def test_improvement_material_defaults() -> None:
    m = ImprovementMaterial(development=[1, 2], improvement_res=[3, 4])
    assert m.development == [1, 2]
    assert m.improvement_res == [3, 4]
    assert m.item_id is None
    assert m.item_name is None
    assert m.item_count is None


def test_improvement_upgrade_defaults() -> None:
    u = ImprovementUpgrade(level=0)
    assert u.level == 0
    assert u.target_id is None
    assert u.target_name is None


def test_improvement_entry_with_optional_fields() -> None:
    entry = ImprovementEntry()
    assert entry.upgrade is None
    assert entry.recipes == []
    assert entry.materials == []
    assert entry.fuel is None
    assert entry.ammo is None
    assert entry.steel is None
    assert entry.bauxite is None


def test_improvement_data_round_trip() -> None:
    """完整 ImprovementData JSON 往返。"""
    data = ImprovementData(
        equip_id=87,
        entries=[
            ImprovementEntry(
                upgrade=ImprovementUpgrade(level=0, target_id=228, target_name="升级"),
                recipes=[
                    ImprovementRecipe(day=[True, False] * 3 + [True], secretary_names=["凤翔"]),
                ],
                materials=[
                    ImprovementMaterial(
                        development=[1, 2], improvement_res=[1, 1],
                        item_id=35, item_name="x", item_count=2,
                    ),
                ],
                fuel=70, ammo=70, steel=70, bauxite=70,
            )
        ],
    )
    j = data.model_dump_json()
    restored = ImprovementData.model_validate_json(j)
    assert restored.equip_id == 87
    assert len(restored.entries) == 1
    entry = restored.entries[0]
    assert entry.upgrade is not None
    assert entry.upgrade.target_id == 228
    assert entry.fuel == 70
    assert len(entry.recipes) == 1
    assert len(entry.recipes[0].day) == 7
    assert entry.materials[0].item_count == 2
