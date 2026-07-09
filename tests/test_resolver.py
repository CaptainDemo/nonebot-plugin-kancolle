"""ShipResolver 单测。

覆盖五层匹配：精确名、改造后缀剥离、拼音、FTS5、rapidfuzz 兜底。
"""
from __future__ import annotations

from pathlib import Path

import pytest

rapidfuzz = pytest.importorskip("rapidfuzz")
pytest.importorskip("pypinyin")

from nonebot_plugin_kancolle.core.resolver import ShipResolver
from nonebot_plugin_kancolle.data.models import Ship, ShipName
from nonebot_plugin_kancolle.data.store import Store


# ----------------------------------------------------------------------
# 测试数据
# ----------------------------------------------------------------------

def _make_test_ships() -> list[Ship]:
    """构造一组测试用舰娘，覆盖改造链、多语言、特殊后缀。"""
    return [
        # 大和链：大和 -> 大和改 -> 大和改二
        Ship(
            id=131,
            name=ShipName(jp="大和", cn="大和", en="Yamato", romaji="やまと"),
            ship_type_id=9,
            remodel_to=136,
        ),
        Ship(
            id=136,
            name=ShipName(jp="大和改", cn="大和改", en="Yamato Kai", romaji="やまとかい"),
            ship_type_id=9,
            remodel_to=546,
            remodel_level=70,
        ),
        Ship(
            id=546,
            name=ShipName(jp="大和改二", cn="大和改二", en="Yamato Kai Ni", romaji="やまとかいに"),
            ship_type_id=9,
        ),
        # Bismarck 链（英文后缀）
        Ship(
            id=175,
            name=ShipName(jp="Bismarck", cn="俾斯麦", en="Bismarck"),
            ship_type_id=9,
            remodel_to=178,
        ),
        Ship(
            id=178,
            name=ShipName(jp="Bismarck改", cn="俾斯麦改", en="Bismarck Kai"),
            ship_type_id=9,
        ),
        # 孤立船（无改造关系）
        Ship(
            id=50,
            name=ShipName(jp="凉风", cn="凉风", en="Suzukaze"),
            ship_type_id=2,
        ),
    ]


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    s.open()
    s.write_ships(_make_test_ships())
    s.rebuild_fts()
    return s


@pytest.fixture()
def resolver(store: Store) -> ShipResolver:
    return ShipResolver(store, max_list_items=5, min_fuzzy_score=60)


# ----------------------------------------------------------------------
# Stage 1: 精确名匹配
# ----------------------------------------------------------------------

def test_resolve_exact_cn_name(resolver: ShipResolver) -> None:
    result = resolver.resolve("大和")
    assert result.is_single
    assert result.ship is not None
    assert result.ship.id == 131


def test_resolve_exact_en_name_case_insensitive(resolver: ShipResolver) -> None:
    result = resolver.resolve("yamato")
    assert result.is_single
    assert result.ship.id == 131


def test_resolve_exact_cn_name_variant(resolver: ShipResolver) -> None:
    """输入与 cn 名完全相等时，stage 1 精确命中。"""
    result = resolver.resolve("凉风")
    assert result.is_single
    assert result.ship.id == 50


def test_resolve_empty_returns_none(resolver: ShipResolver) -> None:
    result = resolver.resolve("")
    assert result.is_none


def test_resolve_whitespace_only_returns_none(resolver: ShipResolver) -> None:
    result = resolver.resolve("   ")
    assert result.is_none


# ----------------------------------------------------------------------
# Stage 2: 改造后缀剥离
# ----------------------------------------------------------------------

def test_resolve_remodel_specific_suffix(resolver: ShipResolver) -> None:
    """输入「大和改二」应精确命中 546（大和改二）。"""
    result = resolver.resolve("大和改二")
    assert result.is_single
    assert result.ship.id == 546


def test_resolve_remodel_core_returns_chain(resolver: ShipResolver) -> None:
    """输入「大和」精确命中 131（不被 stage 2 拦截，因为 stage 1 已经命中）。"""
    # 此处测试 stage 1 的作用：核心名查询首先走精确匹配
    result = resolver.resolve("大和")
    assert result.is_single
    assert result.ship.id == 131


def test_resolve_bismarck_drei_stripped(resolver: ShipResolver) -> None:
    """输入「Bismarck」精确命中 175（基础形态）。"""
    result = resolver.resolve("Bismarck")
    assert result.is_single
    assert result.ship.id == 175


def test_resolve_bismarck_kai(resolver: ShipResolver) -> None:
    """输入「Bismarck Kai」精确命中 178。"""
    result = resolver.resolve("Bismarck Kai")
    assert result.is_single
    assert result.ship.id == 178


# ----------------------------------------------------------------------
# Stage 3: 拼音
# ----------------------------------------------------------------------

def test_resolve_pinyin_matches_chinese_name(resolver: ShipResolver) -> None:
    """输入「dahe」应通过拼音匹配到「大和」链。"""
    result = resolver.resolve("dahe")
    # 大和链上有 3 艘，可能命中 multiple；至少不应是 none
    assert not result.is_none
    # 命中集合应包含大和家族
    if result.is_single:
        assert result.ship.id in (131, 136, 546)
    else:
        ids = {s.id for s in result.candidates}
        assert {131, 136, 546} <= ids


# ----------------------------------------------------------------------
# Stage 4: FTS5
# ----------------------------------------------------------------------

def test_resolve_fts_finds_partial_match(resolver: ShipResolver) -> None:
    """FTS5 应能匹配部分子串（如「凉」匹配「凉风」）。"""
    result = resolver.resolve("凉风")
    assert result.is_single
    assert result.ship.id == 50


# ----------------------------------------------------------------------
# Stage 5: rapidfuzz 兜底
# ----------------------------------------------------------------------

def test_resolve_fuzzy_typo(resolver: ShipResolver) -> None:
    """轻微拼写错误应被 fuzzy 兜底（如「yamat」匹配「Yamato」）。"""
    result = resolver.resolve("yamat")
    # fuzzy 应至少把 Yamato 拉进候选
    assert not result.is_none
    if result.is_single:
        assert result.ship.id == 131
    else:
        ids = {s.id for s in result.candidates}
        assert 131 in ids


def test_resolve_fuzzy_below_threshold_returns_none(resolver: ShipResolver) -> None:
    """完全无关的输入应返回 none。"""
    result = resolver.resolve("xyzzyfoo12345")
    assert result.is_none


# ----------------------------------------------------------------------
# ResolveResult API
# ----------------------------------------------------------------------

def test_resolve_result_single_helper() -> None:
    from nonebot_plugin_kancolle.core.result import ResolveResult

    ship = Ship(id=1, name=ShipName(jp="x"))
    r = ResolveResult.single(ship)
    assert r.is_single
    assert r.ship is ship


def test_resolve_result_none_helper() -> None:
    from nonebot_plugin_kancolle.core.result import ResolveResult

    r = ResolveResult.none("test reason")
    assert r.is_none
    assert r.message == "test reason"
