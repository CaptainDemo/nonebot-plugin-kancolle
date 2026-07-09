"""pypinyin 工具单测。"""
from __future__ import annotations

import pytest

pypinyin = pytest.importorskip("pypinyin")  # 缺 pypinyin 时整模块跳过

from nonebot_plugin_kancolle.utils.pinyin import to_pinyin


def test_basic_chinese_to_pinyin() -> None:
    """常见汉字能转出无声调连写拼音。"""
    assert to_pinyin("大和") == "dahe"
    assert to_pinyin("睦月") == "muyue"
    assert to_pinyin("岛风") == "daofeng"


def test_empty_returns_empty() -> None:
    assert to_pinyin("") == ""


def test_pure_ascii_preserved() -> None:
    """英文字母/数字/符号原样保留。"""
    assert to_pinyin("yamato") == "yamato"
    assert to_pinyin("Yamato K2") == "yamato k2"


def test_mixed_chinese_ascii() -> None:
    """中英混合：中文部分转拼音，英文保留。"""
    result = to_pinyin("大和kai")
    assert "dahe" in result
    assert "kai" in result


def test_lowercase() -> None:
    """结果统一小写。"""
    assert to_pinyin("中国") == "zhongguo"
