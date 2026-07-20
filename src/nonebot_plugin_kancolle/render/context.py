"""模板上下文构建器：把 Ship/Equipment + Store 转为 jinja2 渲染所需的纯 dict。

与 formatter（文本输出）平行存在：P5 阶段 single 模式走图片，context 模块负责
生成模板需要的所有数据；数值条宽度、改造链节点、当前形态标记等都在这里算好。

P7 新增装备 context builders。
P7.1 合并 basic + stats 为一张卡，并新增改修卡 context builder。

输出全部为 dict（不用 dataclass），便于 jinja2 直接展开。
"""
from __future__ import annotations

from typing import Any

from ..data.models import Equipment, ImprovementData, Ship, ShipEnhancement
from ..data.sources.stype_table import get_stype
from ..data.store import Store

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


def _speed_text(speed: int | None) -> str | None:
    if speed is None:
        return None
    return {5: "慢速", 10: "快速", 15: "快速+", 20: "极速"}.get(speed, f"代号{speed}")


def _range_text(range_: int | None) -> str | None:
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


# ======================================================================
# 装备上下文（P7）
# ======================================================================

# 装备数值的满刻度，用于计算条宽比例。
# 装备数值通常远小于舰娘（满级 ~99），刻度相应调小。
EQUIP_STAT_SCALES: dict[str, int] = {
    "firepower": 25,
    "torpedo": 20,
    "aa": 30,
    "armor": 20,
    "asw": 15,
    "los": 15,
    "evasion": 30,
    "accuracy": 20,
    "luck": 10,
    "bombing": 20,
}


def build_equipment_basic_context(
    equip: Equipment,
    type_entry: dict[str, object] | None,
    theme: str,
) -> dict[str, Any]:
    """装备基础卡上下文（P7.1 合并版）。

    合并原 basic + stats 内容为一张更密集的卡片：
    - 头部：96x96 图标位 + 名字 + 类型 + 稀有度
    - 核心 6 数值（带条形图）
    - 完整 10 数值（紧凑网格）
    - 飞机 distance/cost + 废弃返还 4 元素
    """
    broken_labels = ["燃料", "弹药", "钢材", "铝"]
    broken_pairs: list[dict[str, Any]] = []
    if equip.broken:
        for label, value in zip(broken_labels, equip.broken, strict=False):
            broken_pairs.append({"label": label, "value": value})

    return {
        "theme": theme,
        "equip_id": equip.id,
        "name_jp": equip.name.jp,
        "name_cn": equip.name.cn,
        "name_en": equip.name.en,
        "display_name": _equipment_display_name(equip),
        "type_cn": _equipment_type_cn(type_entry),
        "type_jp": _equipment_type_jp(type_entry),
        "type_en": _equipment_type_en(type_entry),
        "rarity": equip.rarity,
        "rarity_label": _rarity_label(equip.rarity),
        # 核心 6 数值（带条形图）
        "core_stats": _equipment_stat_rows(
            equip,
            fields=(
                ("火力", "firepower"),
                ("雷装", "torpedo"),
                ("对空", "aa"),
                ("装甲", "armor"),
                ("对潜", "asw"),
                ("索敌", "los"),
            ),
        ),
        # 完整 10 数值（紧凑网格）
        "full_stats": _equipment_stat_rows(
            equip,
            fields=(
                ("火力", "firepower"),
                ("雷装", "torpedo"),
                ("对空", "aa"),
                ("装甲", "armor"),
                ("对潜", "asw"),
                ("索敌", "los"),
                ("回避", "evasion"),
                ("命中", "accuracy"),
                ("运",   "luck"),
                ("爆装", "bombing"),
            ),
        ),
        "range_text": _equip_range_text(equip.range_),
        "distance": equip.distance,
        "cost": equip.cost,
        "broken_pairs": broken_pairs,
        "detail_hint_name": _equipment_primary_name(equip),
    }


def build_improvement_context(
    equip: Equipment,
    improvement: ImprovementData,
    theme: str,
) -> dict[str, Any]:
    """改修卡上下文（P7.1）。

    包含：
    - 改修消耗表（按 ★阶段拆分，2 或 3 段）
    - 秘书舰名单（按 recipe 分组）
    - 星期可用性矩阵（recipe × day）
    - 升级链（如有）
    """
    day_labels = ["一", "二", "三", "四", "五", "六", "日"]

    sections: list[dict[str, Any]] = []
    for i, entry in enumerate(improvement.entries, 1):
        # 基础消耗 pill
        base_pills: list[dict[str, str]] = []
        if entry.fuel is not None:
            base_pills.append({"label": "燃料", "value": str(entry.fuel)})
        if entry.ammo is not None:
            base_pills.append({"label": "弹药", "value": str(entry.ammo)})
        if entry.steel is not None:
            base_pills.append({"label": "钢材", "value": str(entry.steel)})
        if entry.bauxite is not None:
            base_pills.append({"label": "铝", "value": str(entry.bauxite)})

        # ★阶段表头：低 / 中 / (高)
        stage_labels = ["★0-5", "★6-9"]
        if len(entry.materials) >= 3:
            stage_labels.append("★max→升级")

        # 消耗表行：开发资材 / 改修资材 / 消耗装备
        dev_row: list[str] = []
        imp_row: list[str] = []
        item_row: list[str] = []
        for m in entry.materials:
            dev_row.append(f"{m.development[0]}-{m.development[1]}")
            imp_row.append(f"{m.improvement_res[0]}-{m.improvement_res[1]}")
            if m.item_name and m.item_count:
                item_row.append(f"{m.item_name} ×{m.item_count}")
            elif m.item_name:
                item_row.append(m.item_name)
            else:
                item_row.append("-")

        # 秘书舰名单（每个 recipe 一组）
        recipe_groups: list[dict[str, Any]] = []
        for recipe in entry.recipes:
            recipe_groups.append({
                "secretaries": " · ".join(recipe.secretary_names) if recipe.secretary_names else "（任意）",
                "day": recipe.day,
            })

        # 升级链
        upgrade_text = None
        if entry.upgrade and entry.upgrade.target_name:
            lv = entry.upgrade.level
            upgrade_text = f"★+{lv} → 升级为 {entry.upgrade.target_name}"

        sections.append({
            "index": i,
            "has_multiple": len(improvement.entries) > 1,
            "base_pills": base_pills,
            "stage_labels": stage_labels,
            "dev_row": dev_row,
            "imp_row": imp_row,
            "item_row": item_row,
            "recipe_groups": recipe_groups,
            "upgrade_text": upgrade_text,
        })

    return {
        "theme": theme,
        "equip_id": equip.id,
        "display_name": _equipment_display_name(equip),
        "day_labels": day_labels,
        "sections": sections,
        "bonus_placeholder": "★加成暂未集成（按游戏公式计算）",
    }


# ----------------------------------------------------------------------
# 装备内部辅助
# ----------------------------------------------------------------------

def _equipment_display_name(equip: Equipment) -> str:
    """多语言名拼接，空值过滤。"""
    names = [n for n in (equip.name.jp, equip.name.cn, equip.name.en) if n]
    return " / ".join(names) if names else f"#{equip.id}"


def _equipment_primary_name(equip: Equipment) -> str:
    """主展示名：cn 优先，否则 jp，再否则 en，最后回退到 id。"""
    for n in (equip.name.cn, equip.name.jp, equip.name.en):
        if n:
            return n
    return f"#{equip.id}"


def _equipment_type_cn(type_entry: dict[str, object] | None) -> str:
    if not type_entry:
        return "未知"
    return str(type_entry.get("name_cn") or type_entry.get("name_jp") or "未知")


def _equipment_type_jp(type_entry: dict[str, object] | None) -> str | None:
    if not type_entry:
        return None
    jp = type_entry.get("name_jp")
    return str(jp) if jp else None


def _equipment_type_en(type_entry: dict[str, object] | None) -> str | None:
    if not type_entry:
        return None
    en = type_entry.get("name_en")
    return str(en) if en else None


def _rarity_label(rarity: int | None) -> str | None:
    """稀有度代号 → 中文标签（用于颜色/文本展示）。"""
    if rarity is None:
        return None
    return {0: "普通", 1: "C", 2: "UC", 3: "R", 4: "SR", 5: "SSR", 6: "UR", 7: "UR+"}.get(
        rarity, f"★{rarity}"
    )


def _equipment_stat_rows(
    equip: Equipment,
    fields: tuple[tuple[str, str], ...],
) -> list[dict[str, Any]]:
    """生成装备 stat 行数据。

    每行：label / value / value_ratio / missing。
    装备数值是单值（无 max），与舰娘的双值不同。
    """
    rows: list[dict[str, Any]] = []
    for label, attr in fields:
        value = getattr(equip.stats, attr, None)
        scale = EQUIP_STAT_SCALES.get(attr, 20)
        missing = value is None
        rows.append(
            {
                "label": label,
                "value": value,
                "value_ratio": (value / scale) if value and scale else 0.0,
                "missing": missing,
            }
        )
    return rows


def _equip_range_text(range_: int | None) -> str | None:
    """装备射程代号 → 文本。"""
    if range_ is None:
        return None
    return {0: "无", 1: "短", 2: "中", 3: "长", 4: "超长", 5: "超超长"}.get(
        range_, f"代号{range_}"
    )
