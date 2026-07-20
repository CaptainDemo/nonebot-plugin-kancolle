"""ShipResolver / EquipmentResolver 的返回类型。

设计为 frozen dataclass：
- 三种状态：single（唯一命中）/ multiple（多命中，需用户选）/ none（无命中）
- candidates 用 tuple 避免 dataclass 默认值可变陷阱
- hint 给前端展示提示（如「这是改造链，请选择」）

P7 新增 EquipmentResolveResult，与 ResolveResult 平行结构。
未做泛型化（ResolveResult[T]）以避免触碰 frozen dataclass + classmethod，
保持现有 ShipResolver 测试不变。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..data.models import Equipment, Ship

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
    def single(cls, ship: Ship) -> ResolveResult:
        return cls(status="single", ship=ship)

    @classmethod
    def multiple(
        cls, candidates: list[Ship], hint: str | None = None, message: str = ""
    ) -> ResolveResult:
        return cls(
            status="multiple",
            candidates=tuple(candidates),
            hint=hint,
            message=message,
        )

    @classmethod
    def none(cls, message: str = "") -> ResolveResult:
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


@dataclass(frozen=True)
class EquipmentResolveResult:
    """装备解析结果（P7）。

    结构与 ResolveResult 镜像，但持有 Equipment 类型，避免泛型化引入复杂度。
    hint 取值范围："pinyin" / "fts" / "fuzzy"（装备无改造链概念）。
    """

    status: Status
    equipment: Equipment | None = None
    candidates: tuple[Equipment, ...] = ()
    hint: str | None = None
    message: str = ""

    @classmethod
    def single(cls, equipment: Equipment) -> EquipmentResolveResult:
        return cls(status="single", equipment=equipment)

    @classmethod
    def multiple(
        cls, candidates: list[Equipment], hint: str | None = None, message: str = ""
    ) -> EquipmentResolveResult:
        return cls(
            status="multiple",
            candidates=tuple(candidates),
            hint=hint,
            message=message,
        )

    @classmethod
    def none(cls, message: str = "") -> EquipmentResolveResult:
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
