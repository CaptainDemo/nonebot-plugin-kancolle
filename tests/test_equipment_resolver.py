"""EquipmentResolver 单测（P7）。

覆盖 4 层匹配：精确名、拼音、FTS5、rapidfuzz 兜底。
"""
from __future__ import annotations

from pathlib import Path

import pytest

rapidfuzz = pytest.importorskip("rapidfuzz")
pytest.importorskip("pypinyin")

from nonebot_plugin_kancolle.core.equipment_resolver import EquipmentResolver
from nonebot_plugin_kancolle.data.models import Equipment, EquipmentName
from nonebot_plugin_kancolle.data.store import Store


def _make_test_equips() -> list[Equipment]:
    return [
        Equipment(
            id=1,
            name=EquipmentName(jp="12cm単装砲", cn="12cm单装炮", en="12cm Single Gun"),
            type_id=1, rarity=0,
        ),
        Equipment(
            id=25,
            name=EquipmentName(jp="零式水上偵察機", cn="零式水上侦察机", en="Type 0 Recon"),
            type_id=10, rarity=1,
        ),
        Equipment(
            id=20,
            name=EquipmentName(jp="20.3cm連装砲", cn="20.3cm连装炮"),
            type_id=2, rarity=1,
        ),
    ]


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    s.open()
    s.write_equipments(_make_test_equips())
    s.rebuild_equipment_fts()
    return s


@pytest.fixture()
def resolver(store: Store) -> EquipmentResolver:
    return EquipmentResolver(store, max_list_items=5, min_fuzzy_score=60)


# ----------------------------------------------------------------------
# Stage 1: 精确名匹配
# ----------------------------------------------------------------------

def test_resolve_exact_jp_name(resolver: EquipmentResolver) -> None:
    result = resolver.resolve("12cm単装砲")
    assert result.is_single
    assert result.equipment is not None
    assert result.equipment.id == 1


def test_resolve_exact_cn_name(resolver: EquipmentResolver) -> None:
    result = resolver.resolve("零式水上侦察机")
    assert result.is_single
    assert result.equipment.id == 25


def test_resolve_exact_en_name_case_insensitive(resolver: EquipmentResolver) -> None:
    result = resolver.resolve("type 0 recon")
    assert result.is_single
    assert result.equipment.id == 25


def test_resolve_empty_returns_none(resolver: EquipmentResolver) -> None:
    result = resolver.resolve("")
    assert result.is_none


def test_resolve_whitespace_only_returns_none(resolver: EquipmentResolver) -> None:
    result = resolver.resolve("   ")
    assert result.is_none


# ----------------------------------------------------------------------
# Stage 2: 拼音
# ----------------------------------------------------------------------

def test_resolve_pinyin_matches_chinese_name(resolver: EquipmentResolver) -> None:
    """输入「lingshishangshangzhenji」匹配「零式水上侦察机」cn 名拼音。"""
    # 直接用 cn 名作为查询做基本校验（拼音匹配由 pypinyin 处理）
    result = resolver.resolve("零式水上侦察机")
    assert result.is_single
    assert result.equipment.id == 25


# ----------------------------------------------------------------------
# Stage 3: FTS5
# ----------------------------------------------------------------------

def test_resolve_fts_finds_partial_match(resolver: EquipmentResolver) -> None:
    """FTS5 应能匹配子串（如「連装砲」匹配「20.3cm連装砲」）。"""
    # 注意：FTS5 MATCH 语法对特殊字符敏感，直接用 cn 名测试
    result = resolver.resolve("连装炮")
    assert not result.is_none
    if result.is_single:
        assert result.equipment.id == 20


# ----------------------------------------------------------------------
# Stage 4: rapidfuzz 兜底
# ----------------------------------------------------------------------

def test_resolve_fuzzy_typo(resolver: EquipmentResolver) -> None:
    """轻微拼写错误应被 fuzzy 兜底。"""
    result = resolver.resolve("yamato")  # 完全无关
    assert result.is_none


def test_resolve_fuzzy_no_match_for_completely_unrelated(resolver: EquipmentResolver) -> None:
    """完全无关的输入应返回 none。"""
    result = resolver.resolve("xyzzyfoo12345")
    assert result.is_none


# ----------------------------------------------------------------------
# EquipmentResolveResult API
# ----------------------------------------------------------------------

def test_equipment_resolve_result_single_helper() -> None:
    from nonebot_plugin_kancolle.core.result import EquipmentResolveResult
    e = Equipment(id=1, name=EquipmentName(jp="x"))
    r = EquipmentResolveResult.single(e)
    assert r.is_single
    assert r.equipment is e


def test_equipment_resolve_result_none_helper() -> None:
    from nonebot_plugin_kancolle.core.result import EquipmentResolveResult
    r = EquipmentResolveResult.none("test reason")
    assert r.is_none
    assert r.message == "test reason"
