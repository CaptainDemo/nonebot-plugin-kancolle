"""舰种代号 -> 多语言名硬编码表。

数据来源：start2 api_mst_stype 官方固定 22 项，多年稳定。
中文译名以 kcwiki 中文社区约定为准。
此表是「事实上的常量」，不需要外部数据源；如未来游戏新增舰种再补。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StypeEntry:
    """舰种条目。abbr 是国际通用缩写（DD/CL/CA/BB/CV...）。"""
    id: int
    jp: str
    cn: str
    en: str
    abbr: str


STYPE_TABLE: dict[int, StypeEntry] = {
    entry.id: entry for entry in [
        StypeEntry(1,  "海防艦",         "海防舰",     "Coast Defense Ship",   "DE"),
        StypeEntry(2,  "駆逐艦",         "驱逐舰",     "Destroyer",            "DD"),
        StypeEntry(3,  "軽巡洋艦",       "轻巡洋舰",   "Light Cruiser",        "CL"),
        StypeEntry(4,  "重雷装巡洋艦",   "重雷装巡洋舰","Torpedo Cruiser",      "CLT"),
        StypeEntry(5,  "練習巡洋艦",     "练习巡洋舰", "Training Cruiser",     "CT"),
        StypeEntry(6,  "航空巡洋艦",     "航空巡洋舰", "Aviation Cruiser",     "CAV"),
        StypeEntry(7,  "重巡洋艦",       "重巡洋舰",   "Heavy Cruiser",        "CA"),
        StypeEntry(8,  "航空戦艦",       "航空战舰",   "Aviation Battleship",  "BBV"),
        StypeEntry(9,  "戦艦",           "战舰",       "Battleship",           "BB"),
        StypeEntry(10, "超弩級戦艦",     "超弩级战舰", "Super Dreadnought",    "BB"),
        StypeEntry(11, "正規空母",       "正规空母",   "Standard Carrier",     "CV"),
        StypeEntry(12, "軽空母",         "轻空母",     "Light Carrier",        "CVL"),
        StypeEntry(13, "潜水艦",         "潜水艇",     "Submarine",            "SS"),
        StypeEntry(14, "潜水空母",       "潜水空母",   "Submarine Carrier",    "SSV"),
        StypeEntry(15, "補給艦",         "补给舰",     "Supply Ship",          "AO"),
        StypeEntry(16, "水上機母艦",     "水上机母舰", "Seaplane Tender",      "AV"),
        StypeEntry(17, "揚陸艦",         "扬陆舰",     "Amphibious Ship",      "LHA"),
        StypeEntry(18, "装甲空母",       "装甲空母",   "Armored Carrier",      "CVB"),
        StypeEntry(19, "工作艦",         "工作舰",     "Repair Ship",          "AR"),
        StypeEntry(20, "潜水母艦",       "潜水母舰",   "Submarine Tender",     "AS"),
        StypeEntry(21, "練習潜水艦",     "练习潜水艇", "Training Submarine",   "SS"),
        StypeEntry(22, "補給潜水艦",     "补给潜水艇", "Supply Submarine",     "SS"),
    ]
}


def get_stype(stype_id: int) -> StypeEntry | None:
    """根据 stype id 取条目；不存在返回 None。"""
    return STYPE_TABLE.get(stype_id)


def stype_abbr(stype_id: int) -> str:
    """获取缩写（DD/CL/BB...），未知 id 返回 '?'。"""
    entry = STYPE_TABLE.get(stype_id)
    return entry.abbr if entry else "?"
