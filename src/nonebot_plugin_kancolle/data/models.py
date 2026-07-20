"""舰娘/装备数据模型。

第一阶段仅 Ship；P7 起补 Equipment；任务/海域后续阶段补。

设计要点：
- 所有字段（除 id）都用 Optional / 默认值，保证「旧数据 + 新代码」向前兼容。
- start2 中 ship stats 是 [base, max] 数组，分到 stats_base / stats_max 两个子模型。
- ship speed / range_ 是 ship 级别的标量（每艘船固定），不放 stats。
- ship_type_id / ship_class_id 存 id，文本由 stype_table / ship_classes 表运行时解析。
- equipment stats 是单值（装备无等级成长），用独立 EquipmentStats 模型。
- provenance 字段记录每个非空字段的来源（source / version / fetched_at）。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ShipStats(BaseModel):
    """舰娘数值。

    所有字段都可空 —— 不同数据源覆盖范围不同，缺失时上层用 None
    表示「无数据」，渲染时显示「-」而非崩溃。

    注：evasion / asw / los 不在 start2 主数据中（来自动态接口），
    本插件 P2 阶段恒为 None；如有需要后续阶段从其他源补。
    """

    hp: int | None = None              # 耐久
    firepower: int | None = None       # 火力
    torpedo: int | None = None         # 雷装
    aa: int | None = None              # 对空
    armor: int | None = None           # 装甲
    evasion: int | None = None         # 回避（不在 start2）
    asw: int | None = None             # 对潜（不在 start2）
    los: int | None = None             # 索敌（不在 start2）
    luck: int | None = None            # 运
    slot_count: int | None = None      # 装备槽数（2/3/4/5）
    slot_capacity: list[int] | None = None  # 每槽搭载数（如 [0,0,0,0,0]）
    fuel: int | None = None            # 燃料消耗
    ammo: int | None = None            # 弹药消耗


class ShipName(BaseModel):
    """多语言名称。"""

    jp: str | None = None
    cn: str | None = None
    en: str | None = None
    romaji: str | None = None  # 罗马音/假名（start2 api_yomi 字段）


class Ship(BaseModel):
    """舰娘主模型。

    字段分类：
    - 标识：id, name, aliases
    - 分类：ship_type_id, ship_class_id, ship_class_jp（舰级 JP 名，CN 待后续补）
    - 属性：rarity, speed, range_
    - 数值：stats_base（初期值）, stats_max（满级 99 时）
    - 改造：remodel_to / remodel_level / remodel_fuel_cost / remodel_ammo_cost
            remodel_from（反向回溯） / remodel_chain_root（链头）
    - 来源：provenance
    """

    id: int  # 唯一标识，使用游戏内 ship_id

    name: ShipName = Field(default_factory=ShipName)
    aliases: list[str] = Field(default_factory=list)

    # 分类（id + JP 舰级名；中文舰级名后续阶段补）
    ship_type_id: int | None = None  # 2=DD, 7=CA, 9=BB 等，文本由 stype_table 解析
    ship_class_id: int | None = None  # ctype id（如 28=睦月型）
    ship_class_jp: str | None = None  # 舰级 JP 名（如「睦月型」）

    rarity: int | None = None  # 1-7（start2 主数据无，恒为 None；后续从 shipgraph 补）

    speed: int | None = None  # 航速：5=慢/10=快/15=快+/20=极速
    range_: int | None = None  # 射程：1=短/2=中/3=长/4=超长

    stats_base: ShipStats = Field(default_factory=ShipStats)  # 初期值
    stats_max: ShipStats = Field(default_factory=ShipStats)  # 99 级满级值

    # 改造链
    remodel_to: int | None = None  # 改造后 ship_id
    remodel_level: int | None = None  # 改造所需等级
    remodel_fuel_cost: int | None = None  # 改造消耗燃料
    remodel_ammo_cost: int | None = None  # 改造消耗弹药
    remodel_from: int | None = None  # 改造前 ship_id（fusion 反向回溯写入）
    remodel_chain_root: int | None = None  # 改造链起点 ship_id（fusion 计算）

    # Provenance: {field_name: {source: str, version: str, fetched_at: int}}
    provenance: dict[str, dict[str, Any]] = Field(default_factory=dict)


class RemodelSuffix:
    """改造后缀常量（P3 ship_resolver 使用）。

    命名上：CN 是中文社区的写法，JP 是日文原文，EN 是英文社区约定。
    """

    CN = ["改二乙", "改二甲", "改二戊", "改三", "改二", "改"]  # 长前短后
    JP = ["改二乙", "改二甲", "改三", "改二", "改"]
    EN = [
        "kai ni", "kai2", "k2", "kai", "kai Ni",
        "drei", "due", "bis", "two", "zwfi", "zwei",
        "ko", "nata", "tera", "andra", "nuovo", "deux", "Mod.2", "Mk.II",
    ]


class ShipEnhancement(BaseModel):
    """kcwiki 懒加载增强数据。

    仅在用户实际查询某舰娘时按需拉取，缓存到 ship_enhancements 表。
    字段与 kc3 / kcanotify 主数据互补：can_drop / wiki_id 是 kcwiki 独有。
    """

    ship_id: int
    chinese_name: str | None = None  # 备选中文名（kc3 缺时兜底）
    stype_name_chinese: str | None = None  # 舰种中文名（如「驱逐舰」）
    can_drop: bool | None = None  # 是否可掉落/建造
    wiki_id: str | None = None  # kcwiki.cn 页面 id（可拼出详情 URL）
    filename: str | None = None  # 游戏资源文件名（P5 立绘拉取会用）


# ============================================================================
# 装备模型（P7）
# ============================================================================


class EquipmentName(BaseModel):
    """装备多语言名称。"""

    jp: str | None = None
    cn: str | None = None
    en: str | None = None


class EquipmentStats(BaseModel):
    """装备数值（单值，区别于 ShipStats 的 base/max 双值模型）。

    字段与 start2 api_mst_slotitem 字段对应：
    - api_houg -> firepower  火力
    - api_raig -> torpedo    雷装
    - api_tyku -> aa         对空
    - api_souk -> armor      装甲
    - api_tais -> asw        对潜
    - api_saku -> los        索敌
    - api_houk -> evasion    回避
    - api_houm -> accuracy   命中
    - api_luck -> luck       运
    - api_baku -> bombing    爆装

    不复用 ShipStats：装备无 base/max 之分，且 ShipStats 含 hp/slot_count 等
    装备不适用字段，语义不同。
    """

    firepower: int | None = None  # 火力
    torpedo: int | None = None    # 雷装
    aa: int | None = None         # 对空
    armor: int | None = None      # 装甲
    asw: int | None = None        # 对潜
    los: int | None = None        # 索敌
    evasion: int | None = None    # 回避
    accuracy: int | None = None   # 命中
    luck: int | None = None       # 运
    bombing: int | None = None    # 爆装


class Equipment(BaseModel):
    """装备主模型。

    字段分类：
    - 标识：id, name, aliases
    - 分类：type_icon_id（api_type[2] 图标）, type_id（api_type[3] 类型字典）
    - 属性：rarity（0-7）, range_（0-5）
    - 数值：stats（单组，不分 base/max）
    - 飞机特有：distance（半径）, cost（LBAS 配置）
    - 废弃：broken（拆解返还 [燃料,弹药,钢,铝]）
    - 来源：provenance
    """

    id: int  # 唯一标识，使用游戏内 equipment_id

    name: EquipmentName = Field(default_factory=EquipmentName)
    aliases: list[str] = Field(default_factory=list)

    type_icon_id: int | None = None  # api_type[2]，图标分类
    type_id: int | None = None       # api_type[3]，装备类型 id（关联 equipment_types 表）

    rarity: int | None = None  # 0-7
    range_: int | None = None  # 0=无/1=短/2=中/3=长/4=超长/5=超超长

    stats: EquipmentStats = Field(default_factory=EquipmentStats)

    distance: int | None = None  # 飞机半径（仅飞机类装备有）
    cost: int | None = None      # LBAS 配置成本（仅飞机类装备有）
    broken: list[int] | None = None  # 废弃返还 [燃料,弹药,钢,铝]

    provenance: dict[str, dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# 装备改修模型（P7.1）
# ============================================================================


class ImprovementRecipe(BaseModel):
    """单条改修配方（一组秘书舰 + 星期组合）。

    数据来自 kcwikizh/kcwiki-improvement-data 的 improvement[].req[]。
    secretary_names 直接用 kcwiki 的 secretary 字段（中文/日文混合名），
    不反查本地 Ship 表以保留改造形态后缀（如「凤翔改」）。
    """

    day: list[bool]  # 7 元素，周一..周日
    secretary_names: list[str]  # 展示用名称（kcwiki secretary 字段）

    @classmethod
    def normalize_day(cls, raw: list[bool] | None) -> list[bool]:
        """防御性处理 day 数组：截断或填充到 7 元素。

        已知数据 bug：极少数记录的 day 长度异常（如 35），
        强制截断到前 7 元素；不足 7 时填充 False。
        """
        if not raw:
            return [False] * 7
        return [bool(x) for x in raw[:7]] + [False] * max(0, 7 - len(raw))


class ImprovementMaterial(BaseModel):
    """单阶段改修消耗（低星/中星/高星三段，或无升级链的两段）。

    数据来自 improvement[].consume.material[i]。
    - development/improvement_res 是 [下限, 上限] 二元数组
    - item 是消耗装备（id=0 表示无消耗装备）
    """

    development: list[int]  # [开发资材下限, 上限]
    improvement_res: list[int]  # [改修资材下限, 上限]（避免与外层 entry 字段重名）
    item_id: int | None = None
    item_name: str | None = None
    item_count: int | None = None


class ImprovementUpgrade(BaseModel):
    """升级目标（如 ★+10 后升级为另一件装备）。

    数据来自 improvement[].upgrade。level 通常为 0（表示 +10 后可升级）。
    """

    level: int  # 升级所需 ★，通常为 0
    target_id: int | None = None
    target_name: str | None = None


class ImprovementEntry(BaseModel):
    """单条改修条目（一件装备可有多条不同配方）。

    对应 improvement[] 数组的一个元素。
    """

    upgrade: ImprovementUpgrade | None = None
    recipes: list[ImprovementRecipe] = Field(default_factory=list)
    materials: list[ImprovementMaterial] = Field(default_factory=list)
    fuel: int | None = None  # 基础消耗（不随 ★ 变）
    ammo: int | None = None
    steel: int | None = None
    bauxite: int | None = None


class ImprovementData(BaseModel):
    """装备的完整改修数据。

    来自 kcwikizh/kcwiki-improvement-data 的 improve_data.json。
    一件装备可能有多个 improvement 条目（不同配方），全部存入 entries。
    """

    equip_id: int
    entries: list[ImprovementEntry] = Field(default_factory=list)
