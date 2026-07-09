"""fusion 管线单测。

构造内存中的 fake SourceAdapter，验证：
1. 多源合并：kcanotify 主数据 + kc3 翻译正确 join
2. 改造链计算：remodel_from 反向回溯，remodel_chain_root 链头识别
3. 失败降级：单源失败不阻塞其他源
"""
from __future__ import annotations

import time
from typing import Any, Iterator

import httpx
import pytest

from nonebot_plugin_kancolle.data.fusion import (
    _build_ship,
    _compute_remodel_chains,
    run_fusion,
)
from nonebot_plugin_kancolle.data.models import Ship, ShipName, ShipStats
from nonebot_plugin_kancolle.data.sources.base import RawData, SourceAdapter
from nonebot_plugin_kancolle.data.store import Store


# ----------------------------------------------------------------------
# Fake adapters
# ----------------------------------------------------------------------

class FakeKcanotify(SourceAdapter):
    name = "kcanotify"

    def __init__(self, ships_payload: list[dict]) -> None:
        self._payload = ships_payload

    async def fetch(self, client: httpx.AsyncClient) -> RawData:
        return RawData(
            source=self.name,
            version="kc_test",
            fetched_at=int(time.time()),
            payload=self._payload,
        )

    def normalize_ships(self, raw: RawData) -> Iterator[dict[str, Any]]:
        # 直接把 payload 当作已规整的 list of ship dict
        for item in raw.payload:
            yield item

    def priority(self, field: str) -> int:
        return 10 if field in {"stats_base", "remodel_to"} else 1


class FakeKc3(SourceAdapter):
    name = "kc3"

    def __init__(self, name_map: dict[str, dict[str, str | None]]) -> None:
        self._map = name_map

    async def fetch(self, client: httpx.AsyncClient) -> RawData:
        return RawData(
            source=self.name,
            version="kc3_test",
            fetched_at=int(time.time()),
            payload={"name_map": self._map},
        )

    def normalize_ships(self, raw: RawData) -> Iterator[dict[str, Any]]:
        for jp, names in raw.payload["name_map"].items():
            yield {
                "id": None,
                "lookup_jp_name": jp,
                "name": names,
                "provenance": {},
            }

    def priority(self, field: str) -> int:
        return 10 if field in {"name_cn", "name_en"} else 1


# ----------------------------------------------------------------------
# _build_ship
# ----------------------------------------------------------------------

def test_build_ship_merges_kc3_translation() -> None:
    """kcanotify dict + kc3 lookup -> Ship with cn/en filled + provenance。"""
    item = {
        "id": 1,
        "name": {"jp": "睦月", "romaji": "むつき"},
        "ship_type_id": 2,
        "stats_base": {"hp": 13, "firepower": 6},
        "stats_max": {"hp": 24, "firepower": 29},
        "remodel_to": 254,
        "remodel_level": 20,
        "provenance": {
            "name_jp": {"source": "kcanotify", "version": "kc_test", "fetched_at": 0},
            "stats_base": {"source": "kcanotify", "version": "kc_test", "fetched_at": 0},
        },
    }
    name_lookup = {"睦月": {"cn": "睦月", "en": "Mutsuki"}}

    ship = _build_ship(item, name_lookup, kc3_version="kc3_test", kc3_fetched_at=100)

    assert ship.id == 1
    assert ship.name.jp == "睦月"
    assert ship.name.cn == "睦月"
    assert ship.name.en == "Mutsuki"
    assert ship.name.romaji == "むつき"
    assert ship.stats_base.hp == 13
    assert ship.stats_base.firepower == 6
    assert ship.remodel_to == 254
    # provenance 合并了 kc3 的 name_cn / name_en
    assert ship.provenance["name_cn"]["source"] == "kc3"
    assert ship.provenance["name_cn"]["version"] == "kc3_test"
    assert ship.provenance["name_en"]["source"] == "kc3"


def test_build_ship_without_translation_keeps_name_empty() -> None:
    """kc3 lookup 没有当前 JP 名时，cn/en 保持 None。"""
    item = {
        "id": 999,
        "name": {"jp": "未知艦", "romaji": None},
        "provenance": {},
    }
    ship = _build_ship(item, name_lookup={}, kc3_version="", kc3_fetched_at=0)
    assert ship.name.jp == "未知艦"
    assert ship.name.cn is None
    assert ship.name.en is None


# ----------------------------------------------------------------------
# _compute_remodel_chains
# ----------------------------------------------------------------------

def test_remodel_chain_computation_simple_chain() -> None:
    """链 A -> B -> C：每艘船的 chain_root 都应是 A。"""
    a = Ship(id=1, name=ShipName(jp="A"))
    b = Ship(id=2, name=ShipName(jp="B"), remodel_to=1, remodel_level=20)
    # 注：上面的 remodel_to=1 表示 B 改造后变成 A？这里反了。
    # 正确语义：a.remodel_to=b（A 改造成 B），b.remodel_to=c（B 改造成 C）。
    a = Ship(id=1, name=ShipName(jp="A"), remodel_to=2, remodel_level=20)
    b = Ship(id=2, name=ShipName(jp="B"), remodel_to=3, remodel_level=40)
    c = Ship(id=3, name=ShipName(jp="C"))
    ships = {1: a, 2: b, 3: c}

    _compute_remodel_chains(ships)

    # 所有船的 chain_root 都应该是 1（A）
    assert a.remodel_chain_root == 1
    assert b.remodel_chain_root == 1
    assert c.remodel_chain_root == 1

    # remodel_from 反向
    assert a.remodel_from is None  # A 是链头
    assert b.remodel_from == 1
    assert c.remodel_from == 2


def test_remodel_chain_handles_orphan() -> None:
    """没有改造关系的孤立船：自己就是 chain_root。"""
    solo = Ship(id=100, name=ShipName(jp="孤船"))
    ships = {100: solo}
    _compute_remodel_chains(ships)
    assert solo.remodel_chain_root == 100
    assert solo.remodel_from is None


def test_remodel_chain_handles_broken_pointer() -> None:
    """remodel_to 指向不存在的 id（数据残缺）：不崩溃，链头是自己。"""
    orphan = Ship(id=50, name=ShipName(jp="残"), remodel_to=99999)
    ships = {50: orphan}
    _compute_remodel_chains(ships)
    assert orphan.remodel_chain_root == 50
    assert orphan.remodel_from is None


# ----------------------------------------------------------------------
# run_fusion 端到端
# ----------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path) -> Store:
    s = Store(tmp_path / "test.db")
    s.open()
    return s


@pytest.mark.asyncio
async def test_run_fusion_end_to_end(store: Store) -> None:
    """2 个 fake 适配器 + 真实 store：完整 fusion 流程跑通。"""
    kcanotify_payload = [
        {
            "id": 1,
            "name": {"jp": "睦月", "romaji": "むつき"},
            "ship_type_id": 2,
            "stats_base": {"hp": 13, "firepower": 6},
            "stats_max": {"hp": 24, "firepower": 29},
            "remodel_to": 254,
            "remodel_level": 20,
            "provenance": {
                "name_jp": {"source": "kcanotify", "version": "kc_test", "fetched_at": 0},
            },
        },
        {
            "id": 254,
            "name": {"jp": "睦月改", "romaji": "むつきかい"},
            "ship_type_id": 2,
            "stats_base": {"hp": 24, "firepower": 12},
            "stats_max": {"hp": 39, "firepower": 29},
            "provenance": {
                "name_jp": {"source": "kcanotify", "version": "kc_test", "fetched_at": 0},
            },
        },
    ]
    kc3_map = {
        "睦月": {"cn": "睦月", "en": "Mutsuki"},
        "睦月改": {"cn": "睦月改", "en": "Mutsuki Kai"},
    }

    adapters = [
        FakeKcanotify(kcanotify_payload),
        FakeKc3(kc3_map),
    ]

    async with httpx.AsyncClient() as client:
        data_version = await run_fusion(store, adapters, client)

    # data_version 指纹包含两个源
    assert "kcanotify=kc_test" in data_version
    assert "kc3=kc3_test" in data_version

    # 库里有 2 艘舰
    assert store.count_ships() == 2

    # 睦月改的 cn/en 已合并
    kai = store.get_ship(254)
    assert kai is not None
    assert kai.name.cn == "睦月改"
    assert kai.name.en == "Mutsuki Kai"

    # 改造链
    assert kai.remodel_from == 1
    assert kai.remodel_chain_root == 1

    mutsuki = store.get_ship(1)
    assert mutsuki.remodel_to == 254
    assert mutsuki.remodel_chain_root == 1
    assert mutsuki.remodel_from is None

    # sources 表
    sources = store.list_sources()
    assert {s["name"] for s in sources} == {"kcanotify", "kc3"}
    assert all(s["status"] == "ok" for s in sources)

    # meta 的 data_version 已写入
    assert store.get_meta("data_version") == data_version


@pytest.mark.asyncio
async def test_run_fusion_handles_kc3_failure(store: Store) -> None:
    """kc3 拉取失败时，fusion 仍能用 kcanotify 数据完成（CN/EN 名为空但 stats 完整）。"""

    class FailingKc3(FakeKc3):
        async def fetch(self, client: httpx.AsyncClient) -> RawData:
            raise RuntimeError("simulated kc3 outage")

    kcanotify_payload = [
        {
            "id": 1,
            "name": {"jp": "睦月"},
            "stats_base": {"hp": 13},
            "provenance": {},
        }
    ]
    adapters = [FakeKcanotify(kcanotify_payload), FailingKc3({})]

    async with httpx.AsyncClient() as client:
        data_version = await run_fusion(store, adapters, client)

    # fusion 完成，kc3 标 failed
    sources = {s["name"]: s for s in store.list_sources()}
    assert sources["kc3"]["status"] == "failed"
    assert sources["kcanotify"]["status"] == "ok"

    # 舰娘仍在库，但 cn/en 名为空
    ship = store.get_ship(1)
    assert ship is not None
    assert ship.name.jp == "睦月"
    assert ship.name.cn is None
    assert ship.name.en is None
