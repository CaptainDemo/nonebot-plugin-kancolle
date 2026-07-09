"""kc3-translations SourceAdapter 单测。

不依赖网络：构造 RawData（payload 为模拟的 files 字典），验证翻译字典解析。
"""
from __future__ import annotations

import time

import pytest

from nonebot_plugin_kancolle.data.sources.base import RawData
from nonebot_plugin_kancolle.data.sources.kc3translations import Kc3TranslationsAdapter


@pytest.fixture()
def raw() -> RawData:
    return RawData(
        source="kc3",
        version="abc123",  # 模拟 commit_sha
        fetched_at=int(time.time()),
        payload={
            "files": {
                "ships_scn": {
                    "睦月": "睦月",
                    "睦月改": "睦月改",
                    "大和": "大和",
                },
                "ships_en": {
                    "睦月": "Mutsuki",
                    "睦月改": "Mutsuki Kai",
                    "大和": "Yamato",
                    # 仅 en 有的额外条目也应被覆盖
                    "凉风": "Suzukaze",
                },
                "affix_scn": {"suffixes": {"改": "改"}},
                "affix_en": {"suffixes": {"改": " Kai"}},
            }
        },
    )


def test_normalize_covers_union_of_languages(raw: RawData) -> None:
    """输出覆盖 scn ∪ en 的所有 JP 名（去重）。"""
    adapter = Kc3TranslationsAdapter()
    items = list(adapter.normalize_ships(raw))
    jp_names = {it["lookup_jp_name"] for it in items}
    assert jp_names == {"睦月", "睦月改", "大和", "凉风"}


def test_name_pairs_resolved(raw: RawData) -> None:
    """scn + en 双语都能正确解析。"""
    adapter = Kc3TranslationsAdapter()
    by_jp = {it["lookup_jp_name"]: it for it in adapter.normalize_ships(raw)}

    mutsuki = by_jp["睦月"]
    assert mutsuki["name"]["cn"] == "睦月"
    assert mutsuki["name"]["en"] == "Mutsuki"

    yamato = by_jp["大和"]
    assert yamato["name"]["cn"] == "大和"
    assert yamato["name"]["en"] == "Yamato"


def test_partial_translation_only_one_language(raw: RawData) -> None:
    """凉风只有 en 翻译，cn 应为 None。"""
    adapter = Kc3TranslationsAdapter()
    by_jp = {it["lookup_jp_name"]: it for it in adapter.normalize_ships(raw)}
    suzukaze = by_jp["凉风"]
    assert suzukaze["name"]["en"] == "Suzukaze"
    assert suzukaze["name"]["cn"] is None


def test_provenance_records_kc3_source(raw: RawData) -> None:
    """有翻译的字段 provenance 记 kc3 源 + commit_sha。"""
    adapter = Kc3TranslationsAdapter()
    items = list(adapter.normalize_ships(raw))
    mutsuki = next(it for it in items if it["lookup_jp_name"] == "睦月")

    assert mutsuki["provenance"]["name_cn"]["source"] == "kc3"
    assert mutsuki["provenance"]["name_cn"]["version"] == "abc123"
    assert mutsuki["provenance"]["name_en"]["source"] == "kc3"


def test_id_is_none_pending_fusion_lookup(raw: RawData) -> None:
    """kc3 输出 id=None，由 fusion 按 JP 名匹配回填。"""
    adapter = Kc3TranslationsAdapter()
    for item in adapter.normalize_ships(raw):
        assert item["id"] is None


def test_priority_for_name_fields_is_high(raw: RawData) -> None:
    adapter = Kc3TranslationsAdapter()
    assert adapter.priority("name_cn") == 10
    assert adapter.priority("name_en") == 10
    assert adapter.priority("stats_base") == 1  # 不归 kc3 管
