"""装备 Store CRUD + FTS5 单测（P7）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from nonebot_plugin_kancolle.data.models import (
    Equipment, EquipmentName, EquipmentStats,
)
from nonebot_plugin_kancolle.data.store import Store


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    s.open()
    return s


def _make_equipment(
    equip_id: int = 1,
    jp: str = "12cm単装砲",
    cn: str | None = "12cm单装炮",
    en: str | None = "12cm Single Gun",
) -> Equipment:
    return Equipment(
        id=equip_id,
        name=EquipmentName(jp=jp, cn=cn, en=en),
        type_icon_id=1,
        type_id=1,
        rarity=0,
        range_=1,
        stats=EquipmentStats(firepower=1, aa=2),
        broken=[0, 1, 1, 0],
    )


# ----------------------------------------------------------------------
# 写入 / 查询
# ----------------------------------------------------------------------

def test_write_and_get_equipment(store: Store) -> None:
    """写入装备后能按 id 取回。"""
    e = _make_equipment(1)
    assert store.write_equipments([e]) == 1

    got = store.get_equipment(1)
    assert got is not None
    assert got.id == 1
    assert got.name.jp == "12cm単装砲"
    assert got.name.cn == "12cm单装炮"
    assert got.stats.firepower == 1
    assert got.stats.aa == 2
    assert got.broken == [0, 1, 1, 0]


def test_write_equipments_upsert(store: Store) -> None:
    """同 id 二次写入覆盖。"""
    e1 = _make_equipment(1, cn="旧名")
    store.write_equipments([e1])
    e2 = _make_equipment(1, cn="新名")
    store.write_equipments([e2])
    got = store.get_equipment(1)
    assert got is not None
    assert got.name.cn == "新名"


def test_get_equipment_unknown_returns_none(store: Store) -> None:
    assert store.get_equipment(99999) is None


def test_get_equipments_by_ids_batch(store: Store) -> None:
    """批量取，返回 {id: Equipment}。"""
    store.write_equipments([_make_equipment(1), _make_equipment(2), _make_equipment(3)])
    result = store.get_equipments_by_ids([1, 3, 999])  # 含未知 id
    assert set(result.keys()) == {1, 3}
    assert result[1].id == 1
    assert result[3].id == 3


def test_count_equipments(store: Store) -> None:
    assert store.count_equipments() == 0
    store.write_equipments([_make_equipment(1), _make_equipment(2)])
    assert store.count_equipments() == 2


def test_all_equipments(store: Store) -> None:
    store.write_equipments([_make_equipment(5), _make_equipment(1)])
    all_e = store.all_equipments()
    assert len(all_e) == 2
    # 按 id 排序
    assert [e.id for e in all_e] == [1, 5]


# ----------------------------------------------------------------------
# 精确名匹配
# ----------------------------------------------------------------------

def test_find_equipment_by_exact_jp_name(store: Store) -> None:
    store.write_equipments([_make_equipment(1)])
    got = store.find_equipment_by_exact_name("12cm単装砲")
    assert got is not None
    assert got.id == 1


def test_find_equipment_by_exact_cn_name(store: Store) -> None:
    store.write_equipments([_make_equipment(1)])
    got = store.find_equipment_by_exact_name("12cm单装炮")
    assert got is not None
    assert got.id == 1


def test_find_equipment_by_exact_en_name_case_insensitive(store: Store) -> None:
    store.write_equipments([_make_equipment(1)])
    got = store.find_equipment_by_exact_name("12cm single gun")
    assert got is not None
    assert got.id == 1


def test_find_equipment_unknown_returns_none(store: Store) -> None:
    store.write_equipments([_make_equipment(1)])
    assert store.find_equipment_by_exact_name("不存在的装备") is None


def test_find_equipment_empty_string_returns_none(store: Store) -> None:
    assert store.find_equipment_by_exact_name("") is None


# ----------------------------------------------------------------------
# EquipmentType
# ----------------------------------------------------------------------

def test_write_and_get_equipment_type(store: Store) -> None:
    types = [
        {"type_id": 1, "name_jp": "小口径主砲", "name_cn": "小口径主炮", "name_en": "Small Gun"},
        {"type_id": 10, "name_jp": "水上偵察機", "name_cn": "水上侦察机", "name_en": "Seaplane"},
    ]
    assert store.write_equipment_types(types) == 2
    t1 = store.get_equipment_type(1)
    assert t1 is not None
    assert t1["name_cn"] == "小口径主炮"
    t10 = store.get_equipment_type(10)
    assert t10 is not None
    assert t10["name_jp"] == "水上偵察機"
    # 未知 type_id
    assert store.get_equipment_type(99999) is None


def test_all_equipment_types(store: Store) -> None:
    store.write_equipment_types([
        {"type_id": 2, "name_jp": "B"},
        {"type_id": 1, "name_jp": "A"},
    ])
    all_t = store.all_equipment_types()
    # 按 type_id 排序
    assert [t["type_id"] for t in all_t] == [1, 2]


# ----------------------------------------------------------------------
# FTS5
# ----------------------------------------------------------------------

def test_equipment_fts_search_by_jp_name(store: Store) -> None:
    store.write_equipments([_make_equipment(1, jp="零式水上偵察機")])
    store.rebuild_equipment_fts()
    hits = store.search_equipment_fts("零式水上偵察機", limit=5)
    assert hits
    assert hits[0][0] == 1


def test_equipment_fts_search_by_cn_name(store: Store) -> None:
    store.write_equipments([_make_equipment(1, cn="零式水上侦察机")])
    store.rebuild_equipment_fts()
    hits = store.search_equipment_fts("零式水上侦察机", limit=5)
    assert hits
    assert hits[0][0] == 1


def test_equipment_fts_search_empty_query_returns_empty(store: Store) -> None:
    store.write_equipments([_make_equipment(1)])
    store.rebuild_equipment_fts()
    assert store.search_equipment_fts("", limit=5) == []


def test_equipment_fts_search_with_dot_no_syntax_error(store: Store) -> None:
    """查询含 .（如 20.3cm）不应触发 FTS5 语法错误。"""
    store.write_equipments([_make_equipment(1, jp="20.3cm連装砲")])
    store.rebuild_equipment_fts()
    # 之前会抛 sqlite3.OperationalError: fts5: syntax error near "."
    hits = store.search_equipment_fts("20.3cm", limit=5)
    assert any(h[0] == 1 for h in hits)


def test_equipment_fts_search_with_special_chars_falls_back(store: Store) -> None:
    """极端特殊字符查询不崩溃（FTS5 解析失败时返回空）。"""
    store.write_equipments([_make_equipment(1)])
    store.rebuild_equipment_fts()
    # 各种 FTS5 语法字符，不应崩溃
    for q in ('"', "*", "(", ")", "AND", "OR OR OR"):
        store.search_equipment_fts(q, limit=5)


def test_equipment_fts_rebuild_clears_old_entries(store: Store) -> None:
    store.write_equipments([_make_equipment(1)])
    store.rebuild_equipment_fts()
    # 再写入新装备，重建后旧索引被清空重建
    store.write_equipments([_make_equipment(2, jp="新装备")])
    store.rebuild_equipment_fts()
    hits = store.search_equipment_fts("新装备", limit=5)
    assert any(h[0] == 2 for h in hits)


# ----------------------------------------------------------------------
# Round-trip 含 None / 边界
# ----------------------------------------------------------------------

def test_equipment_round_trip_with_all_none_stats(store: Store) -> None:
    """stats 全 None 的装备能正确读写。"""
    e = Equipment(id=99, name=EquipmentName(jp="空白"))
    store.write_equipments([e])
    got = store.get_equipment(99)
    assert got is not None
    assert got.name.jp == "空白"
    assert got.stats.firepower is None
    assert got.broken is None


def test_equipment_round_trip_preserves_broken_null(store: Store) -> None:
    """broken 为 None 时不写入 JSON，读取回来仍是 None。"""
    e = Equipment(id=88, name=EquipmentName(jp="无拆解"))
    store.write_equipments([e])
    got = store.get_equipment(88)
    assert got is not None
    assert got.broken is None
