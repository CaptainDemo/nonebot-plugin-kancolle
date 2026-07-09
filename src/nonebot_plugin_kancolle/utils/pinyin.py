"""拼音工具：中文 -> 无声调连写拼音。

用于：
1. FTS5 索引列 pinyin（store.rebuild_fts 调用）
2. 用户输入「dahe」能匹配到「大和」

依赖 pypinyin。日文汉字（如「睦月」「大和」）按中文读音转换，
与玩家实际输入习惯（中文用户多按中文拼音搜索）一致。
"""
from __future__ import annotations


def to_pinyin(text: str) -> str:
    """将中文文本转为无声调、连写的拼音字符串。

    示例：「大和」→「dahe」；「睦月」→「muyue」。
    非中文字符（英文字母、数字、符号、日文假名等）原样保留。
    空字符串返回空字符串。
    """
    if not text:
        return ""
    # 局部 import：pypinyin 是 P3 才开始用的依赖，P2 测试环境未装时不影响其他模块
    try:
        from pypinyin import lazy_pinyin, Style
    except ImportError:
        # 无 pypinyin 时降级：返回原文（拼音匹配失效，但不影响其他功能）
        return text

    parts = lazy_pinyin(text, style=Style.NORMAL, errors="default")
    return "".join(parts).lower()
