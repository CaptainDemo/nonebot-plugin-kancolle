"""文本辅助函数。"""
from __future__ import annotations


def truncate(text: str, max_len: int, ellipsis: str = "...") -> str:
    """超长文本截断到 max_len（含省略号）。"""
    if len(text) <= max_len:
        return text
    return text[: max_len - len(ellipsis)] + ellipsis


def join_names(names: list[str], sep: str = " / ") -> str:
    """合并多语言名等列表，过滤空值。"""
    return sep.join(n for n in names if n)
