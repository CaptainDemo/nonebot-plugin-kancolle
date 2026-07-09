"""舰娘数据模型（第一阶段仅 Ship；装备/任务/海域后续阶段补）。

设计要点：
- 所有字段（除 id）都用 Optional / 默认值，保证「旧数据 + 新代码」向前兼容。
- start2 中 stats 是 [base, max] 数组，分到 stats_base / stats_max 两个子模型。
- speed / range_ 是 ship 级别的标量（每艘船固定），不放 stats。
- ship_type_id / ship_class_id 存 id，文本由 stype_table / ship_classes 表运行时解析。
- provenance 字段记录每个非空字段的来源（source / version / fetched_at）。
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ShipStats(BaseModel):
    """舰娘数值。

    所有字段都可空 —— 不同数据源覆盖范围不同，缺失时上层用 None
    表示「无数据」，渲染时显示「-」而非崩溃。

    注：evasion / asw / los 不在 start2 主数据中（来自动态接口），
    本插件 P2 阶段恒为 None；如有需要后续阶段从其他源补。
    """

    hp: Optional[int] = None              # 耐久
    firepower: Optional[int] = None       # 火力
    torpedo: Optional[int] = None         # 雷装
    aa: Optional[int] = None              # 对空
    armor: Optional[int] = None           # 装甲
    evasion: Optional[int] = None         # 回避（不在 start2）
    asw: Optional[int] = None             # 对潜（不在 start2）
    los: Optional[int] = None             # 索敌（不在 start2）
    luck: Optional[int] = None            # 运
    slot_count: Optional[int] = None      # 装备槽数（2/3/4/5）
    slot_capacity: Optional[list[int]] = None  # 每槽搭载数（如 [0,0,0,0,0]）
    fuel: Optional[int] = None            # 燃料消耗
    ammo: Optional[int] = None            # 弹药消耗


class ShipName(BaseModel):
    """多语言名称。"""

    jp: Optional[str] = None
    cn: Optional[str] = None
    en: Optional[str] = None
    romaji: Optional[str] = None  # 罗马音/假名（start2 api_yomi 字段）


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
    ship_type_id: Optional[int] = None  # 2=DD, 7=CA, 9=BB 等，文本由 stype_table 解析
    ship_class_id: Optional[int] = None  # ctype id（如 28=睦月型）
    ship_class_jp: Optional[str] = None  # 舰级 JP 名（如「睦月型」）

    rarity: Optional[int] = None  # 1-7（start2 主数据无，恒为 None；后续从 shipgraph 补）

    speed: Optional[int] = None  # 航速：5=慢/10=快/15=快+/20=极速
    range_: Optional[int] = None  # 射程：1=短/2=中/3=长/4=超长

    stats_base: ShipStats = Field(default_factory=ShipStats)  # 初期值
    stats_max: ShipStats = Field(default_factory=ShipStats)  # 99 级满级值

    # 改造链
    remodel_to: Optional[int] = None  # 改造后 ship_id
    remodel_level: Optional[int] = None  # 改造所需等级
    remodel_fuel_cost: Optional[int] = None  # 改造消耗燃料
    remodel_ammo_cost: Optional[int] = None  # 改造消耗弹药
    remodel_from: Optional[int] = None  # 改造前 ship_id（fusion 反向回溯写入）
    remodel_chain_root: Optional[int] = None  # 改造链起点 ship_id（fusion 计算）

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
