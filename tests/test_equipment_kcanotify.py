"""装备相关 kcanotify SourceAdapter 单测（P7）。

不依赖网络：直接构造 RawData（payload 为模拟 start2 子集），
验证 normalize_slotitems / normalize_equiptypes 字段映射。
"""
from __future__ import annotations

import time

import pytest

from nonebot_plugin_kancolle.data.sources.base import RawData
from nonebot_plugin_kancolle.data.sources.kcanotify import KcanotifyAdapter


def _make_start2() -> dict:
    """构造小型 start2 payload，覆盖装备字段映射。"""
    return {
        "api_data": {
            "api_mst_slotitem": [
                # 普通装备：小口径主炮（type[2]=1 图标，type[3]=1 类型）
                {
                    "api_id": 1,
                    "api_name": "12cm単装砲",
                    "api_type": [1, 1, 1, 1, 0],
                    "api_houg": 1,
                    "api_raig": 0,
                    "api_tyku": 2,
                    "api_souk": 0,
                    "api_tais": 0,
                    "api_saku": 0,
                    "api_houk": 1,
                    "api_houm": 1,
                    "api_luck": 0,
                    "api_baku": 0,
                    "api_leng": 1,
                    "api_rare": 0,
                    "api_broken": [0, 1, 1, 0],
                },
                # 飞机：零式水侦（含 distance/cost）
                {
                    "api_id": 25,
                    "api_name": "零式水上偵察機",
                    "api_type": [5, 7, 10, 10, 2],
                    "api_houg": 0,
                    "api_raig": 0,
                    "api_tyku": 1,
                    "api_souk": 0,
                    "api_tais": 2,
                    "api_saku": 5,
                    "api_houk": 0,
                    "api_houm": 1,
                    "api_luck": 0,
                    "api_baku": 0,
                    "api_leng": 0,
                    "api_rare": 1,
                    "api_distance": 10,
                    "api_cost": 4,
                    "api_broken": [0, 0, 0, 4],
                },
            ],
            "api_mst_slotitem_equiptype": [
                {"api_id": 1, "api_name": "小口径主砲", "api_show_flg": 1},
                {"api_id": 10, "api_name": "水上偵察機", "api_show_flg": 1},
            ],
        }
    }


@pytest.fixture()
def raw() -> RawData:
    return RawData(
        source="kcanotify",
        version="test_v1",
        fetched_at=int(time.time()),
        payload=_make_start2(),
    )


def test_normalize_slotitems_yields_two(raw: RawData) -> None:
    adapter = KcanotifyAdapter()
    items = list(adapter.normalize_slotitems(raw))
    assert len(items) == 2
    ids = {it["id"] for it in items}
    assert ids == {1, 25}


def test_slotitem_field_mapping(raw: RawData) -> None:
    """核心字段映射：id/name/type/stats 都正确。"""
    adapter = KcanotifyAdapter()
    by_id = {it["id"]: it for it in adapter.normalize_slotitems(raw)}

    gun = by_id[1]
    assert gun["name"]["jp"] == "12cm単装砲"
    assert gun["type_icon_id"] == 1  # api_type[2]
    assert gun["type_id"] == 1       # api_type[3]
    assert gun["rarity"] == 0
    assert gun["range_"] == 1
    # stats 单值（非 base/max 数组）
    assert gun["stats"]["firepower"] == 1
    assert gun["stats"]["aa"] == 2
    assert gun["stats"]["evasion"] == 1
    assert gun["stats"]["accuracy"] == 1
    assert gun["stats"]["bombing"] == 0
    # 普通装备无 distance/cost
    assert gun["distance"] is None
    assert gun["cost"] is None
    assert gun["broken"] == [0, 1, 1, 0]

    # 飞机有 distance/cost
    plane = by_id[25]
    assert plane["type_id"] == 10
    assert plane["distance"] == 10
    assert plane["cost"] == 4
    assert plane["stats"]["asw"] == 2
    assert plane["stats"]["los"] == 5
    assert plane["broken"] == [0, 0, 0, 4]


def test_slotitem_provenance(raw: RawData) -> None:
    """所有装备字段 provenance 都标 kcanotify。"""
    adapter = KcanotifyAdapter()
    items = list(adapter.normalize_slotitems(raw))
    gun = next(it for it in items if it["id"] == 1)
    for field in ("name_jp", "type_id", "stats", "rarity", "broken"):
        assert field in gun["provenance"]
        assert gun["provenance"][field]["source"] == "kcanotify"
        assert gun["provenance"][field]["version"] == "test_v1"


def test_normalize_equiptypes(raw: RawData) -> None:
    """装备类型字典解析。"""
    adapter = KcanotifyAdapter()
    types = list(adapter.normalize_equiptypes(raw))
    by_id = {t["type_id"]: t for t in types}
    assert by_id[1]["name_jp"] == "小口径主砲"
    assert by_id[10]["name_jp"] == "水上偵察機"
    # kcanotify 只提供 JP 名；cn/en 由 kc3 兜底
    assert by_id[1]["name_cn"] is None
    assert by_id[1]["name_en"] is None


def test_slotitem_priority_for_equipment_fields() -> None:
    """装备字段优先级正确。"""
    adapter = KcanotifyAdapter()
    assert adapter.priority("stats") == 10
    assert adapter.priority("type_id") == 10
    assert adapter.priority("rarity") == 10
    assert adapter.priority("broken") == 10
    # 不归 kcanotify 管的字段（name_cn）
    assert adapter.priority("name_cn") == 1


def test_slotitem_handles_short_type_array() -> None:
    """api_type 长度不足 5 时不崩溃（旧版数据残留）。"""
    adapter = KcanotifyAdapter()
    raw = RawData(
        source="kcanotify",
        version="v",
        fetched_at=0,
        payload={"api_data": {"api_mst_slotitem": [
            {"api_id": 999, "api_name": "短类型", "api_type": [1, 2]},  # 仅 2 元素
            {"api_id": 1000, "api_name": "无类型", "api_type": []},
        ], "api_mst_slotitem_equiptype": []}},
    )
    items = list(adapter.normalize_slotitems(raw))
    assert len(items) == 2
    short = next(it for it in items if it["id"] == 999)
    assert short["type_icon_id"] is None  # 长度不足，无 type_icon
    assert short["type_id"] is None
    no_type = next(it for it in items if it["id"] == 1000)
    assert no_type["type_icon_id"] is None
    assert no_type["type_id"] is None
