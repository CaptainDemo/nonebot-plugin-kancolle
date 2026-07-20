"""装备 fusion 管线单测（P7）。

构造内存中的 fake SourceAdapter，验证：
1. kcanotify 主数据 + kc3 翻译合并
2. 装备类型字典（JP + cn/en）正确 join
3. 单源失败不阻塞
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest

from nonebot_plugin_kancolle.data.fusion import (
    _build_equipment,
    run_equipment_fusion,
)
from nonebot_plugin_kancolle.data.sources.base import RawData, SourceAdapter
from nonebot_plugin_kancolle.data.store import Store


# ----------------------------------------------------------------------
# Fake adapters
# ----------------------------------------------------------------------

class FakeKcanotify(SourceAdapter):
    name = "kcanotify"

    def __init__(
        self,
        items_payload: list[dict],
        types_payload: list[dict] | None = None,
    ) -> None:
        self._items = items_payload
        self._types = types_payload or []

    async def fetch(self, client: httpx.AsyncClient) -> RawData:
        return RawData(
            source=self.name,
            version="kc_test",
            fetched_at=int(time.time()),
            payload={"items": self._items, "types": self._types},
        )

    def normalize_ships(self, raw: RawData) -> Iterator[dict[str, Any]]:
        return iter([])  # 装备 fusion 不关心 ship

    def normalize_slotitems(self, raw: RawData) -> Iterator[dict[str, Any]]:
        for item in raw.payload["items"]:
            yield item

    def normalize_equiptypes(self, raw: RawData) -> Iterator[dict[str, Any]]:
        for t in raw.payload["types"]:
            yield t

    def priority(self, field: str) -> int:
        return 10 if field in {"stats", "type_id"} else 1


class FakeKc3(SourceAdapter):
    name = "kc3"

    def __init__(
        self,
        items_map: dict[str, dict[str, str | None]],
        types_list: list[dict] | None = None,
    ) -> None:
        self._items_map = items_map
        self._types = types_list or []

    async def fetch(self, client: httpx.AsyncClient) -> RawData:
        return RawData(
            source=self.name,
            version="kc3_test",
            fetched_at=int(time.time()),
            payload={"items_map": self._items_map, "types": self._types},
        )

    def normalize_ships(self, raw: RawData) -> Iterator[dict[str, Any]]:
        return iter([])

    def normalize_slotitems(self, raw: RawData) -> Iterator[dict[str, Any]]:
        for jp, names in raw.payload["items_map"].items():
            yield {
                "id": None,
                "lookup_jp_name": jp,
                "name": names,
                "provenance": {},
            }

    def normalize_equiptypes(self, raw: RawData) -> Iterator[dict[str, Any]]:
        for t in raw.payload["types"]:
            yield t

    def priority(self, field: str) -> int:
        return 10 if field in {"name_cn", "name_en"} else 1


# ----------------------------------------------------------------------
# _build_equipment
# ----------------------------------------------------------------------

def test_build_equipment_merges_kc3_translation() -> None:
    item = {
        "id": 1,
        "name": {"jp": "12cm単装砲"},
        "type_id": 1,
        "rarity": 0,
        "stats": {"firepower": 1},
        "provenance": {
            "name_jp": {"source": "kcanotify", "version": "kc_test", "fetched_at": 0},
        },
    }
    name_lookup = {"12cm単装砲": {"cn": "12cm单装炮", "en": "12cm Single Gun"}}

    equip = _build_equipment(item, name_lookup, kc3_version="kc3_test", kc3_fetched_at=100)

    assert equip.id == 1
    assert equip.name.jp == "12cm単装砲"
    assert equip.name.cn == "12cm单装炮"
    assert equip.name.en == "12cm Single Gun"
    assert equip.stats.firepower == 1
    assert equip.provenance["name_cn"]["source"] == "kc3"
    assert equip.provenance["name_en"]["version"] == "kc3_test"


def test_build_equipment_without_translation_keeps_name_empty() -> None:
    item = {"id": 999, "name": {"jp": "未知装備"}, "provenance": {}}
    equip = _build_equipment(item, name_lookup={}, kc3_version="", kc3_fetched_at=0)
    assert equip.name.jp == "未知装備"
    assert equip.name.cn is None
    assert equip.name.en is None


# ----------------------------------------------------------------------
# run_equipment_fusion 端到端
# ----------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    s.open()
    return s


@pytest.mark.asyncio
async def test_run_equipment_fusion_end_to_end(store: Store) -> None:
    kcanotify_items = [
        {
            "id": 1,
            "name": {"jp": "12cm単装砲"},
            "type_icon_id": 1,
            "type_id": 1,
            "rarity": 0,
            "range_": 1,
            "stats": {"firepower": 1, "aa": 2},
            "broken": [0, 1, 1, 0],
            "provenance": {"name_jp": {"source": "kcanotify", "version": "kc_test", "fetched_at": 0}},
        },
        {
            "id": 25,
            "name": {"jp": "零式水上偵察機"},
            "type_id": 10,
            "rarity": 1,
            "stats": {"asw": 2, "los": 5},
            "distance": 10,
            "provenance": {"name_jp": {"source": "kcanotify", "version": "kc_test", "fetched_at": 0}},
        },
    ]
    kcanotify_types = [
        {"type_id": 1, "name_jp": "小口径主砲", "name_cn": None, "name_en": None, "provenance": {}},
        {"type_id": 10, "name_jp": "水上偵察機", "name_cn": None, "name_en": None, "provenance": {}},
    ]
    kc3_items_map = {
        "12cm単装砲": {"cn": "12cm单装炮", "en": "12cm Single Gun"},
        "零式水上偵察機": {"cn": "零式水上侦察机", "en": "Type 0 Recon"},
    }
    kc3_types = [
        {"type_id": 1, "name_cn": "小口径主炮", "name_en": "Small Gun", "name_jp": None, "provenance": {}},
        {"type_id": 10, "name_cn": "水上侦察机", "name_en": "Seaplane", "name_jp": None, "provenance": {}},
    ]

    # 模拟已拉取的 raws（实际场景由 _fetch_all_adapters 产出）
    kc_adapter = FakeKcanotify(kcanotify_items, kcanotify_types)
    kc3_adapter = FakeKc3(kc3_items_map, kc3_types)
    raws = {
        "kcanotify": await kc_adapter.fetch(httpx.AsyncClient()),
        "kc3": await kc3_adapter.fetch(httpx.AsyncClient()),
    }

    n = await run_equipment_fusion(store, [kc_adapter, kc3_adapter], raws)

    assert n == 2
    assert store.count_equipments() == 2

    # 装备翻译已合并
    gun = store.get_equipment(1)
    assert gun is not None
    assert gun.name.cn == "12cm单装炮"
    assert gun.name.en == "12cm Single Gun"
    assert gun.broken == [0, 1, 1, 0]

    plane = store.get_equipment(25)
    assert plane is not None
    assert plane.name.cn == "零式水上侦察机"
    assert plane.distance == 10

    # 类型字典已合并
    t1 = store.get_equipment_type(1)
    assert t1 is not None
    assert t1["name_jp"] == "小口径主砲"
    assert t1["name_cn"] == "小口径主炮"
    assert t1["name_en"] == "Small Gun"

    # FTS 重建
    hits = store.search_equipment_fts("12cm単装砲", limit=5)
    assert any(h[0] == 1 for h in hits)


@pytest.mark.asyncio
async def test_run_equipment_fusion_handles_kc3_failure(store: Store) -> None:
    """kc3 拉取失败时，fusion 仍能用 kcanotify 数据完成（cn/en 名为空）。"""

    class FailingKc3(FakeKc3):
        async def fetch(self, client: httpx.AsyncClient) -> RawData:
            raise RuntimeError("simulated kc3 outage")

    kc_adapter = FakeKcanotify(
        [{"id": 1, "name": {"jp": "x"}, "provenance": {}}],
        [],
    )
    raws = {"kcanotify": await kc_adapter.fetch(httpx.AsyncClient())}

    # kc3 缺失：raws 中无 kc3 key
    n = await run_equipment_fusion(store, [kc_adapter, FailingKc3({})], raws)

    assert n == 1
    e = store.get_equipment(1)
    assert e is not None
    assert e.name.jp == "x"
    assert e.name.cn is None
    assert e.name.en is None
