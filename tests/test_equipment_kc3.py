"""装备相关 kc3-translations SourceAdapter 单测（P7）。"""
from __future__ import annotations

import time

import pytest

from nonebot_plugin_kancolle.data.sources.base import RawData
from nonebot_plugin_kancolle.data.sources.kc3translations import Kc3TranslationsAdapter


@pytest.fixture()
def raw() -> RawData:
    """模拟 kc3 fetch 后的 payload：含 items.json + equiptype.json。"""
    return RawData(
        source="kc3",
        version="abc123",
        fetched_at=int(time.time()),
        payload={
            "files": {
                # 装备名翻译
                "items_scn": {
                    "12cm単装砲": "12cm单装炮",
                    "零式水上偵察機": "零式水上侦察机",
                },
                "items_en": {
                    "12cm単装砲": "12cm Single Gun Mount",
                    "零式水上偵察機": "Type 0 Recon Seaplane",
                    # 仅 en 有
                    "九六式艦戦": "Type 96 Fighter",
                },
                # 装备类型翻译：5 元素数组，每元素是按 type id 索引的字符串列表
                "equiptype_scn": [
                    [], [], [],  # type[0..2]
                    [None, "小口径主炮", None, None, None, None, None, None,
                     None, None, "水上侦察机"],  # 索引 [3]，索引 1=小口径，10=水侦
                ],
                "equiptype_en": [
                    [], [], [],
                    [None, "Small Caliber Main Gun", None, None, None, None,
                     None, None, None, None, "Seaplane Recon"],
                ],
                # 舰娘相关字段（P7 之前就有）
                "ships_scn": {},
                "ships_en": {},
                "affix_scn": {"suffixes": {}},
                "affix_en": {"suffixes": {}},
            }
        },
    )


def test_normalize_slotitems_covers_union_of_languages(raw: RawData) -> None:
    """输出覆盖 scn ∪ en 的所有 JP 名。"""
    adapter = Kc3TranslationsAdapter()
    items = list(adapter.normalize_slotitems(raw))
    jp_names = {it["lookup_jp_name"] for it in items}
    assert jp_names == {"12cm単装砲", "零式水上偵察機", "九六式艦戦"}


def test_slotitem_name_pairs_resolved(raw: RawData) -> None:
    """scn + en 双语解析。"""
    adapter = Kc3TranslationsAdapter()
    by_jp = {it["lookup_jp_name"]: it for it in adapter.normalize_slotitems(raw)}
    gun = by_jp["12cm単装砲"]
    assert gun["name"]["cn"] == "12cm单装炮"
    assert gun["name"]["en"] == "12cm Single Gun Mount"


def test_slotitem_partial_translation(raw: RawData) -> None:
    """仅 en 有的装备，cn 应为 None。"""
    adapter = Kc3TranslationsAdapter()
    by_jp = {it["lookup_jp_name"]: it for it in adapter.normalize_slotitems(raw)}
    fighter = by_jp["九六式艦戦"]
    assert fighter["name"]["en"] == "Type 96 Fighter"
    assert fighter["name"]["cn"] is None


def test_slotitem_provenance(raw: RawData) -> None:
    """有翻译的字段 provenance 记 kc3 源 + commit_sha。"""
    adapter = Kc3TranslationsAdapter()
    items = list(adapter.normalize_slotitems(raw))
    gun = next(it for it in items if it["lookup_jp_name"] == "12cm単装砲")
    assert gun["provenance"]["name_cn"]["source"] == "kc3"
    assert gun["provenance"]["name_cn"]["version"] == "abc123"


def test_normalize_equiptypes_uses_index_3(raw: RawData) -> None:
    """equiptype.json 索引 [3] 的类型名被正确解析。"""
    adapter = Kc3TranslationsAdapter()
    types = list(adapter.normalize_equiptypes(raw))
    by_id = {t["type_id"]: t for t in types}
    # type_id=1 -> 小口径主炮
    assert by_id[1]["name_cn"] == "小口径主炮"
    assert by_id[1]["name_en"] == "Small Caliber Main Gun"
    # type_id=10 -> 水上侦察机
    assert by_id[10]["name_cn"] == "水上侦察机"
    assert by_id[10]["name_en"] == "Seaplane Recon"
    # kc3 不提供 JP 名（由 fusion 从 kcanotify 兜底）
    assert by_id[1]["name_jp"] is None


def test_equiptype_skips_both_missing_indices(raw: RawData) -> None:
    """cn/en 都为 None 的索引不输出。"""
    adapter = Kc3TranslationsAdapter()
    types = list(adapter.normalize_equiptypes(raw))
    type_ids = {t["type_id"] for t in types}
    # 索引 0、2-9 都为 None，应被跳过；只剩 1 和 10
    assert type_ids == {1, 10}
