"""ShipResolver 的返回类型。

设计为 frozen dataclass：
- 三种状态：single（唯一命中）/ multiple（多命中，需用户选）/ none（无命中）
- candidates 用 tuple 避免 dataclass 默认值可变陷阱
- hint 给前端展示提示（如「这是改造链，请选择」）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..data.models import Ship


Status = Literal["single", "multiple", "none"]


@dataclass(frozen=True)
class ResolveResult:
    """舰娘解析结果。"""

    status: Status
    ship: Ship | None = None
    candidates: tuple[Ship, ...] = ()
    hint: str | None = None  # "chain" / "pinyin" / "fts" / "fuzzy"，便于前端给提示
    message: str = ""

    @classmethod
    def single(cls, ship: Ship) -> "ResolveResult":
        return cls(status="single", ship=ship)

    @classmethod
    def multiple(
        cls, candidates: list[Ship], hint: str | None = None, message: str = ""
    ) -> "ResolveResult":
        return cls(
            status="multiple",
            candidates=tuple(candidates),
            hint=hint,
            message=message,
        )

    @classmethod
    def none(cls, message: str = "") -> "ResolveResult":
        return cls(status="none", message=message)

    @property
    def is_single(self) -> bool:
        return self.status == "single"

    @property
    def is_multiple(self) -> bool:
        return self.status == "multiple"

    @property
    def is_none(self) -> bool:
        return self.status == "none"
