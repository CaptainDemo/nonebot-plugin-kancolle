"""kcanotify SourceAdapter 单测。

不依赖网络：直接构造 RawData（payload 为模拟的 start2 子集），
验证 normalize_ships 字段映射正确性。
"""
from __future__ import annotations

import time

import pytest

from nonebot_plugin_kancolle.data.sources.base import RawData
from nonebot_plugin_kancolle.data.sources.kcanotify import KcanotifyAdapter


def _make_start2() -> dict:
    """构造一个小型 start2 payload，覆盖典型场景：基础舰、改造链、缺失字段。"""
    return {
        "api_data": {
            "api_mst_ship": [
                # 睦月（基础形态，改造到 254）
                {
                    "api_id": 1,
                    "api_name": "睦月",
                    "api_yomi": "むつき",
                    "api_stype": 2,
                    "api_ctype": 28,
                    "api_afterlv": 20,
                    "api_aftershipid": "254",
                    "api_taik": [13, 24],
                    "api_souk": [5, 18],
                    "api_houg": [6, 29],
                    "api_raig": [18, 59],
                    "api_tyku": [7, 29],
                    "api_luck": [12, 49],
                    "api_soku": 10,
                    "api_leng": 1,
                    "api_slot_num": 2,
                    "api_maxeq": [0, 0, 0, 0, 0],
                    "api_fuel_max": 15,
                    "api_bull_max": 15,
                    "api_afterfuel": 100,
                    "api_afterbull": 100,
                },
                # 睦月改（改造形态，无进一步改造）
                {
                    "api_id": 254,
                    "api_name": "睦月改",
                    "api_yomi": "むつきかい",
                    "api_stype": 2,
                    "api_ctype": 28,
                    "api_afterlv": 0,
                    "api_aftershipid": "",
                    "api_taik": [24, 39],
                    "api_souk": [11, 38],
                    "api_houg": [12, 39],
                    "api_raig": [28, 69],
                    "api_tyku": [20, 49],
                    "api_luck": [12, 49],
                    "api_soku": 10,
                    "api_leng": 1,
                    "api_slot_num": 3,
                    "api_maxeq": [0, 0, 0, 0, 0],
                    "api_fuel_max": 15,
                    "api_bull_max": 15,
                },
            ],
            "api_mst_ctype": [
                {"api_id": 28, "api_name": "睦月型", "api_sortno": 28},
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


def test_normalize_yields_two_ships(raw: RawData) -> None:
    adapter = KcanotifyAdapter()
    ships = list(adapter.normalize_ships(raw))
    assert len(ships) == 2
    ids = {s["id"] for s in ships}
    assert ids == {1, 254}


def test_field_mapping(raw: RawData) -> None:
    """核心字段映射：id/name/stats/speed/remodel_to 都正确。"""
    adapter = KcanotifyAdapter()
    ships = {s["id"]: s for s in adapter.normalize_ships(raw)}

    mutsuki = ships[1]
    assert mutsuki["name"]["jp"] == "睦月"
    assert mutsuki["name"]["romaji"] == "むつき"
    assert mutsuki["ship_type_id"] == 2
    assert mutsuki["ship_class_id"] == 28
    assert mutsuki["ship_class_jp"] == "睦月型"
    assert mutsuki["speed"] == 10
    assert mutsuki["range_"] == 1
    # stats_base / stats_max 数值
    assert mutsuki["stats_base"]["hp"] == 13
    assert mutsuki["stats_max"]["hp"] == 24
    assert mutsuki["stats_base"]["firepower"] == 6
    assert mutsuki["stats_max"]["firepower"] == 29
    assert mutsuki["stats_base"]["fuel"] == 15
    assert mutsuki["stats_base"]["ammo"] == 15
    assert mutsuki["stats_base"]["slot_count"] == 2
    assert mutsuki["stats_base"]["slot_capacity"] == [0, 0, 0, 0, 0]
    # 改造链
    assert mutsuki["remodel_to"] == 254
    assert mutsuki["remodel_level"] == 20
    assert mutsuki["remodel_fuel_cost"] == 100
    assert mutsuki["remodel_ammo_cost"] == 100

    # 改造形态无后续
    kai = ships[254]
    assert kai["remodel_to"] is None
    assert kai["remodel_level"] is None


def test_provenance_records_kcanotify_source(raw: RawData) -> None:
    """所有由本适配器填充的字段 provenance 都标 kcanotify。"""
    adapter = KcanotifyAdapter()
    ships = list(adapter.normalize_ships(raw))
    mutsuki = next(s for s in ships if s["id"] == 1)

    for field in ("name_jp", "stats_base", "stats_max", "remodel_to"):
        assert field in mutsuki["provenance"]
        assert mutsuki["provenance"][field]["source"] == "kcanotify"
        assert mutsuki["provenance"][field]["version"] == "test_v1"


def test_priority_for_stats_is_high() -> None:
    """stats / 改造链字段优先级最高。"""
    adapter = KcanotifyAdapter()
    assert adapter.priority("stats_base") == 10
    assert adapter.priority("remodel_to") == 10
    # 其他字段（如 name_cn）不是本源负责
    assert adapter.priority("name_cn") == 1
