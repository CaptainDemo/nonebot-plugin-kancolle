"""纯文本格式化函数。

不依赖 nonebot / alconna / httpx，便于单元测试。
handler 调用这些函数生成最终输出文本。

输出约定：
- 使用 Unicode 装饰符（▸ / → / ✓ / ✗），避免彩色 emoji 在不同 IM 渲染不一致
- 数值字段缺失时统一显示 '-'（与设计文档一致：保留行，标缺失）
- 多语言名只展示非空字段，避免空斜杠
"""
from __future__ import annotations

from ..core.result import EquipmentResolveResult, ResolveResult
from ..data.models import Equipment, ImprovementData, Ship, ShipEnhancement
from ..data.sources.stype_table import get_stype
from ..data.store import Store

# ----------------------------------------------------------------------
# 单舰娘输出
# ----------------------------------------------------------------------

def format_basic(ship: Ship, enhancement: ShipEnhancement | None = None) -> str:
    """默认模式：基础卡（数值概览）。"""
    header = _ship_header(ship)
    stats = _stats_compact(ship)
    footer = _footer_with_hints(ship, enhancement, basic=True)
    return f"{header}\n\n{stats}\n\n{footer}"


def format_detail(ship: Ship, enhancement: ShipEnhancement | None = None) -> str:
    """详细模式：完整数值 + 装备 + 改造信息。"""
    header = _ship_header(ship)
    stats = _stats_full(ship)
    equip = _equipment_block(ship)
    remodel = _remodel_inline(ship)
    footer = _footer_with_hints(ship, enhancement, basic=False)
    return f"{header}\n\n{stats}\n\n{equip}\n\n{remodel}\n\n{footer}"


def format_remodel(ship: Ship, store: Store) -> str:
    """改造链模式：显示完整改造链 + 当前位置。"""
    chain = _get_chain_ships(ship, store)
    if not chain:
        return f"无法构造「{_display_name(ship)}」的改造链（数据缺失）"

    title = f"{_display_name(ship)} 改造链"
    chain_str = _render_chain_timeline(chain, ship)
    hint = "\n回复对应舰名查看卡片"

    return f"{title}\n\n{chain_str}{hint}"


# ----------------------------------------------------------------------
# 多命中输出
# ----------------------------------------------------------------------

def format_multiple(result: ResolveResult) -> str:
    """多命中列表。提示用户重发精确名（不实现编号选择机制）。"""
    assert result.is_multiple
    candidates = list(result.candidates)
    query_hint = ""
    if result.hint == "chain":
        query_hint = "（同名改造链家族）"
    elif result.hint == "pinyin":
        query_hint = "（拼音匹配）"
    elif result.hint == "fts":
        query_hint = "（全文匹配）"
    elif result.hint == "fuzzy":
        query_hint = "（模糊匹配）"

    header = f"找到 {len(candidates)} 艘相关舰娘{query_hint}:\n"
    lines = []
    for i, s in enumerate(candidates, 1):
        lines.append(f"[{i}] {_display_name(s):20s}  {_ship_brief(s)}")
    footer = "\n请直接发送具体舰名查看详情"
    return header + "\n".join(lines) + footer


# ----------------------------------------------------------------------
# 帮助
# ----------------------------------------------------------------------

def format_help_overview() -> str:
    """帮助总览。"""
    return (
        "== 舰队Collection 信息查询 ==\n"
        "\n"
        "▸ 查询\n"
        "  查舰娘 <名>            ship <name>\n"
        "    查询舰娘基础信息\n"
        "  查舰娘 <名> 详细        ship <name> -d\n"
        "    完整数值与装备详情\n"
        "  查舰娘 <名> 改造        ship <name> remodel\n"
        "    显示改造链\n"
        "  查装备 <名>            equip <name>\n"
        "    查询装备基础信息\n"
        "  查装备 <名> 详细        equip <name> -d\n"
        "    完整数值与废弃返还\n"
        "\n"
        "▸ 帮助\n"
        "  舰C帮助                kchelp\n"
        "    显示本帮助\n"
        "  舰C帮助 <指令名>       kchelp <cmd>\n"
        "    查看具体指令用法\n"
        "\n"
        "▸ 数据管理 (仅 SUPERUSER)\n"
        "  更新舰娘数据            kancolle update\n"
        "    立即拉取最新数据\n"
        "  数据状态                kancolle status\n"
        "    查看各数据源状态\n"
        "\n"
        "提示:\n"
        "- 支持中日英名、罗马音、拼音\n"
        "- 改造型号模糊匹配 (如「大和改二」/「yamato k2」)\n"
        "- 详细用法: 舰C帮助 查舰娘"
    )


def format_help_topic(topic: str) -> str | None:
    """特定指令帮助。未知 topic 返回 None（由 handler 决定如何兜底）。"""
    topics = {
        "查舰娘": _help_ship,
        "ship": _help_ship,
        "查装备": _help_equipment,
        "equip": _help_equipment,
        "舰c帮助": _help_help,
        "kchelp": _help_help,
        "更新舰娘数据": _help_update,
        "kancolle update": _help_update,
        "数据状态": _help_status,
        "kancolle status": _help_status,
    }
    fn = topics.get(topic.strip().lower())
    return fn() if fn else None


def _help_ship() -> str:
    return (
        "== 查舰娘 / ship ==\n"
        "\n"
        "查询舰娘数据，返回图片卡片。\n"
        "\n"
        "用法:\n"
        "  查舰娘 <名>\n"
        "  查舰娘 <名> 详细\n"
        "  查舰娘 <名> 改造\n"
        "  ship <name>\n"
        "  ship <name> -d\n"
        "  ship <name> remodel\n"
        "\n"
        "参数:\n"
        "  <名>  必填。支持中日英名、罗马音、拼音、模糊匹配\n"
        "\n"
        "示例:\n"
        "  查舰娘 大和\n"
        "  查舰娘 大和改二\n"
        "  查舰娘 yamato k2\n"
        "  ship yamato -d\n"
        "  查舰娘 大和 改造"
    )


def _help_equipment() -> str:
    return (
        "== 查装备 / equip ==\n"
        "\n"
        "查询装备数据，返回图片卡片。\n"
        "\n"
        "用法:\n"
        "  查装备 <名>\n"
        "  查装备 <名> 详细\n"
        "  equip <name>\n"
        "  equip <name> -d\n"
        "\n"
        "参数:\n"
        "  <名>  必填。支持中日英名、拼音、模糊匹配\n"
        "\n"
        "示例:\n"
        "  查装备 零式水上偵察機\n"
        "  查装备 20.3cm连装炮 详细\n"
        "  equip Type 0 Recon\n"
        "\n"
        "说明:\n"
        "- 基础卡含完整数值 + 飞机半径/成本 + 废弃返还\n"
        "- 详细模式额外显示改修数据（消耗、秘书舰、星期可用性）\n"
        "- 多命中时返回候选列表，请重发精确名"
    )


def _help_help() -> str:
    return (
        "== 舰C帮助 / kchelp ==\n"
        "\n"
        "查看指令用法。\n"
        "\n"
        "用法:\n"
        "  舰C帮助\n"
        "  舰C帮助 <指令名>\n"
        "  kchelp\n"
        "  kchelp <cmd>\n"
        "\n"
        "<指令名> 可选值:\n"
        "  查舰娘 / ship\n"
        "  更新舰娘数据 / kancolle update\n"
        "  数据状态 / kancolle status"
    )


def _help_update() -> str:
    return (
        "== 更新舰娘数据 / kancolle update ==\n"
        "\n"
        "立即拉取最新舰娘数据（kcanotify + kc3-translations）。\n"
        "\n"
        "权限: 仅 SUPERUSER\n"
        "\n"
        "用法:\n"
        "  更新舰娘数据\n"
        "  kancolle update\n"
        "\n"
        "说明:\n"
        "- 数据每周自动检查更新；本指令用于手动触发\n"
        "- 拉取耗时约 10-30 秒，取决于网络\n"
        "- 更新成功后会自动失效旧的图片缓存"
    )


def _help_status() -> str:
    return (
        "== 数据状态 / kancolle status ==\n"
        "\n"
        "查看各数据源的版本、最近更新时间、舰娘总数。\n"
        "\n"
        "用法:\n"
        "  数据状态\n"
        "  kancolle status"
    )


# ----------------------------------------------------------------------
# 更新与状态（P6 新增）
# ----------------------------------------------------------------------

def format_update_result(result: object) -> str:
    """格式化 UpdateResult（来自 update.pipeline）。"""
    # 用 duck typing 避免上层强类型依赖
    error = getattr(result, "error", None)
    if error:
        return (
            f"✗ 更新失败\n"
            f"\n"
            f"错误: {error[:200]}\n"
            f"\n"
            f"可稍后重试「更新舰娘数据」"
        )

    changed = getattr(result, "changed", False)
    version = getattr(result, "data_version", "")
    ship_count = getattr(result, "ship_count", 0)
    equip_count = getattr(result, "equip_count", 0)
    cache_inv = getattr(result, "cache_invalidated", 0)

    if changed:
        cache_line = f"\n缓存失效: {cache_inv} 张图" if cache_inv else ""
        return (
            f"✓ 更新完成\n"
            f"\n"
            f"数据版本: {version}\n"
            f"舰娘总数: {ship_count}\n"
            f"装备总数: {equip_count}{cache_line}"
        )

    return (
        f"✓ 数据已是最新\n"
        f"\n"
        f"数据版本: {version}\n"
        f"舰娘总数: {ship_count}\n"
        f"装备总数: {equip_count}"
    )


def format_data_status(
    data_version: str,
    ship_count: int,
    sources: list[object],
    equip_count: int = 0,
) -> str:
    """格式化「数据状态」指令的输出。

    sources 元素需有 name / version / status / fetched_at / item_count / error_msg。
    equip_count 默认 0 以保持向后兼容（旧调用方不传时仅不显示装备行）。
    """
    from datetime import datetime

    lines = ["== 数据状态 ==", ""]

    if not data_version:
        lines.append("数据版本: (尚未拉取)")
    else:
        lines.append(f"数据版本: {data_version}")
    lines.append(f"舰娘总数: {ship_count}")
    lines.append(f"装备总数: {equip_count}")

    # 整体最后更新时间：取 sources 中最大的 fetched_at
    fetched_ats = [
        int(getattr(s, "fetched_at", 0) or 0)
        for s in sources
        if getattr(s, "fetched_at", 0)
    ]
    if fetched_ats:
        last = max(fetched_ats)
        lines.append(f"最后更新: {datetime.fromtimestamp(last).strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    if not sources:
        lines.append("▸ 数据源: (无记录，请执行「更新舰娘数据」)")
    else:
        lines.append("▸ 数据源")
        for s in sources:
            name = getattr(s, "name", "?")
            status = getattr(s, "status", "?")
            version = getattr(s, "version", "") or "(无版本)"
            fetched_at = int(getattr(s, "fetched_at", 0) or 0)
            item_count = int(getattr(s, "item_count", 0) or 0)
            error_msg = getattr(s, "error_msg", "") or ""

            icon = _status_icon(status)
            lines.append(f"  {name}")
            lines.append(f"    版本: {version}")
            lines.append(f"    状态: {icon} {status}")
            if fetched_at:
                ts = datetime.fromtimestamp(fetched_at).strftime("%Y-%m-%d %H:%M:%S")
                lines.append(f"    拉取: {ts}")
            if item_count:
                lines.append(f"    数量: {item_count}")
            if error_msg:
                lines.append(f"    错误: {error_msg[:120]}")

    return "\n".join(lines)


def _status_icon(status: str) -> str:
    """数据源状态对应的视觉标记。"""
    if status == "ok":
        return "✓"
    if status == "failed":
        return "✗"
    if status == "stale":
        return "⚠"
    if status == "pending":
        return "○"
    return "?"


# ----------------------------------------------------------------------
# 内部辅助
# ----------------------------------------------------------------------

def _ship_header(ship: Ship) -> str:
    """舰娘卡片头部：多语言名 + 舰种 / 舰级 / id。"""
    name = _display_name(ship)
    brief = _ship_brief(ship)
    return f"{name}\n{brief}"


def _display_name(ship: Ship) -> str:
    """多语言名拼接，空值过滤。例：「大和 / ヤマト / Yamato」。"""
    names = [n for n in (ship.name.jp, ship.name.cn, ship.name.en) if n]
    return " / ".join(names) if names else f"舰娘 #{ship.id}"


def _ship_brief(ship: Ship) -> str:
    """单行简介：「舰种 舰级 · ID xxx」。"""
    parts: list[str] = []
    if ship.ship_type_id is not None:
        entry = get_stype(ship.ship_type_id)
        if entry:
            parts.append(f"{entry.cn} {entry.abbr}")
    if ship.ship_class_jp:
        parts.append(ship.ship_class_jp)
    brief = " · ".join(parts) if parts else "未知"
    return f"{brief} · ID {ship.id}"


def _stats_compact(ship: Ship) -> str:
    """紧凑数值表（默认卡用）。格式「字段  base → max」对齐。"""
    rows = _stats_rows(ship, fields=(
        ("耐久",   "hp"),
        ("火力",   "firepower"),
        ("雷装",   "torpedo"),
        ("对空",   "aa"),
        ("装甲",   "armor"),
        ("运",     "luck"),
    ))
    return "\n".join(_format_stat_row(r) for r in rows)


def _stats_full(ship: Ship) -> str:
    """完整数值表（详细卡用）。"""
    header = "▸ 数值\n"
    rows = _stats_rows(ship, fields=(
        ("耐久",   "hp"),
        ("火力",   "firepower"),
        ("雷装",   "torpedo"),
        ("对空",   "aa"),
        ("装甲",   "armor"),
        ("回避",   "evasion"),
        ("对潜",   "asw"),
        ("索敌",   "los"),
        ("运",     "luck"),
    ))
    body = "\n".join(_format_stat_row_two_col(r) for r in rows)
    # 附加航速/射程（ship 级别属性）
    extras: list[str] = []
    if ship.speed is not None:
        extras.append(f"航速: {_speed_text(ship.speed)}")
    if ship.range_ is not None:
        extras.append(f"射程: {_range_text(ship.range_)}")
    extras_line = "\n".join(extras)
    return f"{header}{body}\n{extras_line}" if extras else f"{header}{body}"


def _stats_rows(
    ship: Ship,
    fields: tuple[tuple[str, str], ...],
) -> list[tuple[str, int | None, int | None]]:
    """取 stats_base / stats_max 同名字段。"""
    rows = []
    for label, attr in fields:
        base = getattr(ship.stats_base, attr, None)
        mx = getattr(ship.stats_max, attr, None)
        rows.append((label, base, mx))
    return rows


def _format_stat_row(row: tuple[str, int | None, int | None]) -> str:
    """单行紧凑格式：「耐久   93 → 108」。"""
    label, base, mx = row
    base_s = _int_str(base)
    max_s = _int_str(mx)
    # 不变化时只显示一个值，避免冗余
    if base == mx:
        return f"{label:<4s}  {base_s}"
    return f"{label:<4s}  {base_s} → {max_s}"


def _format_stat_row_two_col(row: tuple[str, int | None, int | None]) -> str:
    """双列对齐：「耐久         93       108」."""
    label, base, mx = row
    return f"{label:<6s}  {_int_str(base):>6s}    {_int_str(mx):>6s}"


def _equipment_block(ship: Ship) -> str:
    """装备信息块。"""
    lines = ["▸ 装备"]
    if ship.stats_base.slot_count is not None:
        lines.append(f"  槽数: {ship.stats_base.slot_count}")
    if ship.stats_base.slot_capacity is not None:
        lines.append(f"  搭载: {ship.stats_base.slot_capacity}")
    if ship.stats_base.fuel is not None:
        lines.append(f"  燃料消耗: {ship.stats_base.fuel}")
    if ship.stats_base.ammo is not None:
        lines.append(f"  弹药消耗: {ship.stats_base.ammo}")
    if len(lines) == 1:
        lines.append("  (无装备数据)")
    return "\n".join(lines)


def _remodel_inline(ship: Ship) -> str:
    """详细卡里改造信息的简短展示（不是完整改造链）。"""
    lines = ["▸ 改造"]
    if ship.remodel_to and ship.remodel_level:
        lines.append(f"  → 改造后: #{ship.remodel_to} (Lv {ship.remodel_level})")
    elif ship.remodel_to:
        lines.append(f"  → 改造后: #{ship.remodel_to}")
    else:
        lines.append("  (无后续改造)")
    if ship.remodel_from:
        lines.append(f"  ← 改造前: #{ship.remodel_from}")
    lines.append("  完整链: 「查舰娘 " + (_display_name(ship).split(" / ")[0] or "") + " 改造」")
    return "\n".join(lines)


def _footer_with_hints(
    ship: Ship, enhancement: ShipEnhancement | None, basic: bool
) -> str:
    """卡片底部：可获取状态 + 详情提示。"""
    lines: list[str] = []
    if enhancement is not None:
        if enhancement.can_drop is True:
            lines.append("✓ 可获取")
        elif enhancement.can_drop is False:
            lines.append("✗ 当前不可获取")
    if basic:
        # 默认卡提示如何看详情
        first_name = (_display_name(ship).split(" / ")[0] or "").strip()
        if first_name:
            lines.append(f"💡 详情: 「查舰娘 {first_name} 详细」")
    return "\n".join(lines) if lines else ""


def _get_chain_ships(ship: Ship, store: Store) -> list[Ship]:
    """从链头开始，按改造顺序遍历整条链。"""
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


def _render_chain_timeline(chain: list[Ship], current: Ship) -> str:
    """渲染「[1] A ─ Lv.X → [2] B ─ Lv.Y → [3] C」并标注当前位置。"""
    parts: list[str] = []
    for i, s in enumerate(chain, 1):
        is_current = s.id == current.id
        marker = "▸" if is_current else " "
        name = _display_name(s).split(" / ")[0] or f"#{s.id}"
        parts.append(f"{marker} [{i}] {name} (ID {s.id})")
        if s.remodel_to and i < len(chain):
            lv = s.remodel_level if s.remodel_level else "?"
            parts.append(f"      └─ Lv.{lv} ─┘")
    cur_marker = "▲ 当前位置"
    return "\n".join(parts) + f"\n{cur_marker}" if any(s.id == current.id for s in chain) else "\n".join(parts)


def _int_str(v: int | None) -> str:
    """整数转字符串；None → '-'。"""
    return str(v) if v is not None else "-"


def _speed_text(speed: int) -> str:
    """航速代号 → 文本。"""
    return {5: "慢速", 10: "快速", 15: "快+", 20: "极速"}.get(speed, f"代号{speed}")


def _range_text(range_: int) -> str:
    """射程代号 → 文本。"""
    return {1: "短", 2: "中", 3: "长", 4: "超长"}.get(range_, f"代号{range_}")


# ----------------------------------------------------------------------
# 装备输出（P7）
# ----------------------------------------------------------------------

def format_equipment_basic(
    equip: Equipment, type_entry: dict[str, object] | None = None
) -> str:
    """装备基础卡（P7.1 合并版）：完整数值 + 飞机/废弃信息。

    合并原 basic + stats 文本内容，与图片基础卡信息对齐。
    """
    header = _equipment_header(equip, type_entry)
    stats = _equipment_stats_full(equip)
    extras = _equipment_extras(equip)
    footer = _equipment_footer(equip, basic=True)
    parts = [header, "", stats, "", extras]
    if footer:
        parts.extend(["", footer])
    return "\n".join(parts)


def format_equipment_detail(
    equip: Equipment,
    type_entry: dict[str, object] | None = None,
    improvement: ImprovementData | None = None,
) -> str:
    """装备详细卡（P7.1）：基础信息 + 改修数据。

    improvement 为 None 时仅显示基础信息 + 提示「暂无改修数据」。
    """
    basic = format_equipment_basic(equip, type_entry)
    if improvement is None:
        return basic + "\n\n⚠ 暂无改修数据"

    improvement_text = _format_improvement(improvement)
    return basic + "\n\n" + improvement_text


def _format_improvement(improvement: ImprovementData) -> str:
    """格式化改修数据为文本。"""
    day_labels = ["一", "二", "三", "四", "五", "六", "日"]
    lines = ["▸ 改修数据"]
    for i, entry in enumerate(improvement.entries, 1):
        if len(improvement.entries) > 1:
            lines.append(f"  配方 {i}")

        # 基础消耗
        base_parts = []
        if entry.fuel is not None:
            base_parts.append(f"燃料 {entry.fuel}")
        if entry.ammo is not None:
            base_parts.append(f"弹药 {entry.ammo}")
        if entry.steel is not None:
            base_parts.append(f"钢材 {entry.steel}")
        if entry.bauxite is not None:
            base_parts.append(f"铝 {entry.bauxite}")
        if base_parts:
            lines.append("  基础消耗: " + " / ".join(base_parts))

        # 改修资材表（按阶段）
        for j, m in enumerate(entry.materials):
            stage = ["★0-5", "★6-9", "★max→升级"][j] if j < 3 else f"阶段{j}"
            lines.append(
                f"  {stage}: 开发 {m.development[0]}-{m.development[1]} / "
                f"改修 {m.improvement_res[0]}-{m.improvement_res[1]}"
                + (f" / {m.item_name} ×{m.item_count}" if m.item_name else "")
            )

        # 升级链
        if entry.upgrade and entry.upgrade.target_name:
            lines.append(f"  升级: ★+{entry.upgrade.level} → {entry.upgrade.target_name}")

        # 秘书舰 + 星期
        for recipe in entry.recipes:
            days = "·".join(
                day_labels[k] for k, ok in enumerate(recipe.day) if ok and k < 7
            ) or "无"
            secr = " · ".join(recipe.secretary_names) if recipe.secretary_names else "任意"
            lines.append(f"  秘书舰: {secr}  星期: {days}")

    lines.append("  (★ 加成数值暂未集成)")
    return "\n".join(lines)


def format_equipment_multiple(result: EquipmentResolveResult) -> str:
    """多命中列表（装备）。"""
    assert result.is_multiple
    candidates = list(result.candidates)
    query_hint = ""
    if result.hint == "pinyin":
        query_hint = "（拼音匹配）"
    elif result.hint == "fts":
        query_hint = "（全文匹配）"
    elif result.hint == "fuzzy":
        query_hint = "（模糊匹配）"

    header = f"找到 {len(candidates)} 件相关装备{query_hint}:\n"
    lines = []
    for i, e in enumerate(candidates, 1):
        lines.append(f"[{i}] {_equipment_display_name(e):30s}  {_equipment_brief(e)}")
    footer = "\n请直接发送具体装备名查看详情"
    return header + "\n".join(lines) + footer


# ----------------------------------------------------------------------
# 装备内部辅助
# ----------------------------------------------------------------------

_EQIP_STAT_FIELDS_BASIC = (
    ("火力", "firepower"),
    ("雷装", "torpedo"),
    ("对空", "aa"),
    ("装甲", "armor"),
    ("对潜", "asw"),
    ("索敌", "los"),
)

_EQUIP_STAT_FIELDS_FULL = (
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
)


def _equipment_header(
    equip: Equipment, type_entry: dict[str, object] | None
) -> str:
    """装备卡头部：多语言名 + 类型 + id。"""
    name = _equipment_display_name(equip)
    brief = _equipment_brief(equip, type_entry)
    return f"{name}\n{brief}"


def _equipment_display_name(equip: Equipment) -> str:
    """装备多语言名拼接，空值过滤。"""
    names = [n for n in (equip.name.jp, equip.name.cn, equip.name.en) if n]
    return " / ".join(names) if names else f"装备 #{equip.id}"


def _equipment_brief(
    equip: Equipment, type_entry: dict[str, object] | None = None
) -> str:
    """单行简介：「类型中文名 · 稀有度 · ID xxx」。"""
    parts: list[str] = []
    if type_entry:
        cn = type_entry.get("name_cn") or type_entry.get("name_jp")
        if cn:
            parts.append(str(cn))
    if equip.rarity is not None:
        parts.append(f"★{equip.rarity}")
    brief = " · ".join(parts) if parts else "未知"
    return f"{brief} · ID {equip.id}"


def _equipment_stats_compact(equip: Equipment) -> str:
    """紧凑数值表（基础卡用）。"""
    rows = [
        (label, getattr(equip.stats, attr, None))
        for label, attr in _EQIP_STAT_FIELDS_BASIC
    ]
    lines = [f"{label:<4s}  {_int_str(val)}" for label, val in rows]
    # 射程
    if equip.range_ is not None:
        lines.append(f"射程  {_equip_range_text(equip.range_)}")
    return "\n".join(lines)


def _equipment_stats_full(equip: Equipment) -> str:
    """完整数值表（详细卡用）。"""
    header = "▸ 数值\n"
    rows = [
        (label, getattr(equip.stats, attr, None))
        for label, attr in _EQUIP_STAT_FIELDS_FULL
    ]
    body = "\n".join(f"{label:<4s}  {_int_str(val):>4s}" for label, val in rows)
    # 射程
    extras: list[str] = []
    if equip.range_ is not None:
        extras.append(f"射程: {_equip_range_text(equip.range_)}")
    if equip.rarity is not None:
        extras.append(f"稀有度: ★{equip.rarity}")
    extras_line = "\n".join(extras)
    return f"{header}{body}\n{extras_line}" if extras else f"{header}{body}"


def _equipment_extras(equip: Equipment) -> str:
    """飞机 distance/cost + 废弃返还。"""
    lines = ["▸ 额外"]
    has_extra = False
    if equip.distance is not None:
        lines.append(f"  半径: {equip.distance}")
        has_extra = True
    if equip.cost is not None:
        lines.append(f"  配置成本: {equip.cost}")
        has_extra = True
    if equip.broken:
        labels = ["燃料", "弹药", "钢材", "铝"]
        items = [f"{lb} {v}" for lb, v in zip(labels, equip.broken, strict=False)]
        lines.append("  废弃返还: " + " / ".join(items))
        has_extra = True
    if not has_extra:
        lines.append("  (无额外数据)")
    return "\n".join(lines)


def _equipment_footer(equip: Equipment, basic: bool) -> str:
    """卡片底部：详情提示。"""
    if not basic:
        return ""
    first_name = (_equipment_display_name(equip).split(" / ")[0] or "").strip()
    if first_name:
        return f"💡 详情:「查装备 {first_name} 详细」"
    return ""


def _equip_range_text(range_: int) -> str:
    """装备射程代号 → 文本（0=无 是装备特有）。"""
    return {0: "无", 1: "短", 2: "中", 3: "长", 4: "超长", 5: "超超长"}.get(
        range_, f"代号{range_}"
    )
