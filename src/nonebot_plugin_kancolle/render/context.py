"""模板上下文构建器：把 Ship + 增强 + Store 转为 jinja2 渲染所需的纯 dict。

与 formatter（文本输出）平行存在：P5 阶段 single 模式走图片，context 模块负责
生成模板需要的所有数据；数值条宽度、改造链节点、当前形态标记等都在这里算好。

输出全部为 dict（不用 dataclass），便于 jinja2 直接展开。
"""
from __future__ import annotations

from typing import Any, Optional

from ..data.models import Ship, ShipEnhancement
from ..data.sources.stype_table import get_stype
from ..data.store import Store
from ..utils.pinyin import to_pinyin

# 各项数值的"满刻度"，用于计算条宽比例（base_ratio = base / scale）
# 取游戏内常见上限，确保大部分舰娘的条不会顶满
STAT_SCALES: dict[str, int] = {
    "hp": 200,
    "firepower": 250,
    "torpedo": 200,
    "aa": 200,
    "armor": 200,
    "evasion": 100,  # start2 不含，预留
    "asw": 100,      # 同上
    "los": 100,      # 同上
    "luck": 100,
}


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------

def build_basic_context(
    ship: Ship,
    enhancement: ShipEnhancement | None,
    theme: str,
    art_data_url: str | None = None,
) -> dict[str, Any]:
    """基础卡上下文：名字 + 简介 + 6 核心数值 + 可获取/提示 + 可选立绘。"""
    return {
        "theme": theme,
        "ship_id": ship.id,
        "name_jp": ship.name.jp,
        "name_cn": ship.name.cn,
        "name_en": ship.name.en,
        "name_romaji": ship.name.romaji,
        "display_name": _display_name(ship),
        "stype_cn": _stype_cn(ship),
        "stype_abbr": _stype_abbr(ship),
        "ship_class_jp": ship.ship_class_jp,
        "stats": _stat_rows(
            ship,
            fields=(
                ("耐久", "hp"),
                ("火力", "firepower"),
                ("雷装", "torpedo"),
                ("对空", "aa"),
                ("装甲", "armor"),
                ("运", "luck"),
            ),
        ),
        "speed_text": _speed_text(ship.speed),
        "range_text": _range_text(ship.range_),
        "can_drop": enhancement.can_drop if enhancement else None,
        "detail_hint_name": _primary_name(ship),
        "art_data_url": art_data_url,  # None 时模板隐藏立绘栏
    }


def build_stats_context(
    ship: Ship, enhancement: ShipEnhancement | None, theme: str
) -> dict[str, Any]:
    """详情数值面板上下文：完整 9 项数值 + 装备 + 改造基础信息。"""
    return {
        "theme": theme,
        "ship_id": ship.id,
        "display_name": _display_name(ship),
        "stype_cn": _stype_cn(ship),
        "stype_abbr": _stype_abbr(ship),
        "ship_class_jp": ship.ship_class_jp,
        "stats": _stat_rows(
            ship,
            fields=(
                ("耐久", "hp"),
                ("火力", "firepower"),
                ("雷装", "torpedo"),
                ("对空", "aa"),
                ("装甲", "armor"),
                ("回避", "evasion"),
                ("对潜", "asw"),
                ("索敌", "los"),
                ("运", "luck"),
            ),
        ),
        "speed_text": _speed_text(ship.speed),
        "range_text": _range_text(ship.range_),
        "slot_count": ship.stats_base.slot_count,
        "slot_capacity": ship.stats_base.slot_capacity,
        "fuel": ship.stats_base.fuel,
        "ammo": ship.stats_base.ammo,
        "remodel_to_id": ship.remodel_to,
        "remodel_level": ship.remodel_level,
        "remodel_from_id": ship.remodel_from,
        "can_drop": enhancement.can_drop if enhancement else None,
    }


def build_remodel_context(ship: Ship, store: Store, theme: str) -> dict[str, Any]:
    """改造链上下文：从链头顺序遍历，标记当前位置。"""
    chain_ships = _get_chain_ships(ship, store)
    nodes: list[dict[str, Any]] = []
    for i, s in enumerate(chain_ships, 1):
        prev = chain_ships[i - 2] if i >= 2 else None
        nodes.append(
            {
                "index": i,
                "ship_id": s.id,
                "name": _primary_name(s),
                "display_name": _display_name(s),
                "stype_abbr": _stype_abbr(s),
                "level_required": prev.remodel_level if prev and prev.remodel_level else None,
                "is_current": s.id == ship.id,
            }
        )
    return {
        "theme": theme,
        "ship_id": ship.id,
        "display_name": _display_name(ship),
        "chain": nodes,
        "chain_length": len(nodes),
    }


# ----------------------------------------------------------------------
# 内部辅助
# ----------------------------------------------------------------------

def _stat_rows(
    ship: Ship,
    fields: tuple[tuple[str, str], ...],
) -> list[dict[str, Any]]:
    """生成 stat 行数据。

    每行包含：label / base / max / base_ratio / max_ratio / missing
    缺失字段（base 与 max 都为 None）标记 missing=True，模板渲染为 '-'。
    """
    rows: list[dict[str, Any]] = []
    for label, attr in fields:
        base = getattr(ship.stats_base, attr, None)
        mx = getattr(ship.stats_max, attr, None)
        scale = STAT_SCALES.get(attr, 100)
        missing = base is None and mx is None
        rows.append(
            {
                "label": label,
                "base": base,
                "max": mx,
                "base_ratio": (base / scale) if base and scale else 0.0,
                "max_ratio": (mx / scale) if mx and scale else 0.0,
                "missing": missing,
            }
        )
    return rows


def _display_name(ship: Ship) -> str:
    """多语言名拼接，空值过滤。"""
    names = [n for n in (ship.name.jp, ship.name.cn, ship.name.en) if n]
    return " / ".join(names) if names else f"#{ship.id}"


def _primary_name(ship: Ship) -> str:
    """主展示名：cn 优先，否则 jp，再否则 en，最后回退到 id。"""
    for n in (ship.name.cn, ship.name.jp, ship.name.en):
        if n:
            return n
    return f"#{ship.id}"


def _stype_cn(ship: Ship) -> str:
    if ship.ship_type_id is None:
        return "未知"
    entry = get_stype(ship.ship_type_id)
    return entry.cn if entry else "未知"


def _stype_abbr(ship: Ship) -> str:
    if ship.ship_type_id is None:
        return "?"
    entry = get_stype(ship.ship_type_id)
    return entry.abbr if entry else "?"


def _speed_text(speed: Optional[int]) -> Optional[str]:
    if speed is None:
        return None
    return {5: "慢速", 10: "快速", 15: "快速+", 20: "极速"}.get(speed, f"代号{speed}")


def _range_text(range_: Optional[int]) -> Optional[str]:
    if range_ is None:
        return None
    return {1: "短", 2: "中", 3: "长", 4: "超长"}.get(range_, f"代号{range_}")


def _get_chain_ships(ship: Ship, store: Store) -> list[Ship]:
    """从链头顺序遍历整条改造链。"""
    root_id = ship.remodel_chain_root or ship.id
    chain: list[Ship] = []
    cur_id: int | None = root_id
    seen: set[int] = set()
    while cur_id is not None and cur_id not in seen:
        seen.add(cur_id)
        cur = store.get_ship(cur_id)
        if cur is None:
            break
        chain.append(cur)
        cur_id = cur.remodel_to
    return chain
