"""kcanotify-gamedata 数据源适配器。

源：https://github.com/antest1/kcanotify-gamedata
- api_start2：完整官方 start2 主数据（舰娘 stats、改造链、舰种/舰级字典等）
- DATA_VERSION：上游数据版本指纹（避免消耗 GitHub API 配额）

字段映射见 normalize_ships 内部注释。
"""
from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from typing import Any

import httpx

from ...utils.logger import log
from .base import RawData, SourceAdapter
from .github import fetch_raw


class KcanotifyAdapter(SourceAdapter):
    """kcanotify-gamedata 适配器。

    主数据源：提供 ship id / JP name / 全 stats / 改造链。
    """

    name = "kcanotify"
    REPO = "antest1/kcanotify-gamedata"
    REF = "master"

    async def fetch(self, client: httpx.AsyncClient) -> RawData:
        """从 kcanotify-gamedata 拉取 api_start2 与 DATA_VERSION。

        DATA_VERSION 是上游维护的版本字符串（如 "2024_11_15"），
        比每次都查 GitHub commit_sha 更省配额。
        """
        import time

        # 并发拉取主文件与版本指纹
        data_resp, ver_resp = await _gather(
            fetch_raw(client, self.REPO, "api_start2", self.REF),
            fetch_raw(client, self.REPO, "DATA_VERSION", self.REF),
        )

        if data_resp.not_modified or not data_resp.body:
            raise RuntimeError("kcanotify api_start2 returned empty body")
        if not ver_resp.body:
            raise RuntimeError("kcanotify DATA_VERSION returned empty body")

        version = _decode_text(ver_resp.body).strip()
        payload = json.loads(_maybe_gunzip(data_resp.body))

        return RawData(
            source=self.name,
            version=version,
            fetched_at=int(time.time()),
            payload=payload,
        )

    def normalize_ships(self, raw: RawData) -> Iterator[dict[str, Any]]:
        """把 start2 api_mst_ship 规整为 Ship 模型 dict 流。

        start2 字段对照（详见 normalize_one_ship 的注释）：
        - api_id -> id
        - api_name -> name.jp
        - api_yomi -> name.romaji
        - api_stype -> ship_type_id
        - api_ctype -> ship_class_id
        - api_houg/raig/tyku/souk/taik/luck -> stats_base/stats_max 的火力/雷装/对空/装甲/HP/运
        - api_soku -> speed；api_leng -> range_
        - api_slot_num -> stats_base.slot_count
        - api_maxeq -> stats_base.slot_capacity
        - api_fuel_max -> stats_base.fuel；api_bull_max -> stats_base.ammo
        - api_aftershipid/afterlv/afterfuel/afterbull -> remodel_to/level/fuel_cost/ammo_cost
        """
        payload = raw.payload
        if not isinstance(payload, dict):
            raise ValueError(f"kcanotify payload must be dict, got {type(payload).__name__}")

        # start2 顶层结构有两种：直接字典，或包一层 {api_data: {...}}
        api_data = payload.get("api_data", payload)
        ships = api_data.get("api_mst_ship", [])
        ctypes = {c["api_id"]: c for c in api_data.get("api_mst_ctype", []) if "api_id" in c}

        for raw_ship in ships:
            try:
                yield _normalize_one_ship(raw_ship, ctypes, raw.version, raw.fetched_at)
            except (KeyError, ValueError, TypeError) as e:
                # 单条异常不影响整体；记录跳过的 id，便于上游诊断
                log.warning(f"kcanotify skip ship {raw_ship.get('api_id')}: {e}")

    def normalize_slotitems(self, raw: RawData) -> Iterator[dict[str, Any]]:
        """把 start2 api_mst_slotitem 规整为 Equipment 模型 dict 流。

        start2 字段对照（详见 _normalize_one_slotitem 的注释）：
        - api_id -> id
        - api_name -> name.jp
        - api_type[2] -> type_icon_id；api_type[3] -> type_id
        - api_houg/raig/tyku/souk/tais/saku/houk/houm/luck/baku -> stats
        - api_leng -> range_；api_rare -> rarity
        - api_distance -> distance；api_cost -> cost
        - api_broken[4] -> broken
        """
        payload = raw.payload
        if not isinstance(payload, dict):
            raise ValueError(f"kcanotify payload must be dict, got {type(payload).__name__}")

        api_data = payload.get("api_data", payload)
        items = api_data.get("api_mst_slotitem", [])

        for raw_item in items:
            try:
                yield _normalize_one_slotitem(raw_item, raw.version, raw.fetched_at)
            except (KeyError, ValueError, TypeError) as e:
                log.warning(f"kcanotify skip slotitem {raw_item.get('api_id')}: {e}")

    def normalize_equiptypes(self, raw: RawData) -> Iterator[dict[str, Any]]:
        """把 start2 api_mst_slotitem_equiptype 规整为 dict 流。

        输出 dict 含 type_id / name_jp（name_cn/name_en 留给 kc3 覆盖）。
        """
        payload = raw.payload
        if not isinstance(payload, dict):
            raise ValueError(f"kcanotify payload must be dict, got {type(payload).__name__}")

        api_data = payload.get("api_data", payload)
        types = api_data.get("api_mst_slotitem_equiptype", [])

        for entry in types:
            try:
                type_id = entry["api_id"]
            except (KeyError, TypeError) as e:
                log.warning(f"kcanotify skip equiptype entry {entry!r}: {e}")
                continue
            yield {
                "type_id": int(type_id),
                "name_jp": entry.get("api_name"),
                "name_cn": None,
                "name_en": None,
                "provenance": {
                    "name_jp": {
                        "source": "kcanotify",
                        "version": raw.version,
                        "fetched_at": raw.fetched_at,
                    }
                } if entry.get("api_name") else {},
            }

    def priority(self, field: str) -> int:
        """kcanotify 是 stats / 改造链 / 标识字段的主源；装备字段同样主源。"""
        if field in {
            "id", "name_jp", "name_romaji", "ship_type_id", "ship_class_id",
            "ship_class_jp", "speed", "range_", "stats_base", "stats_max",
            "remodel_to", "remodel_level", "remodel_fuel_cost", "remodel_ammo_cost",
            # 装备字段（P7）
            "type_icon_id", "type_id", "rarity", "stats",
            "distance", "cost", "broken",
        }:
            return 10
        return 1


def _normalize_one_ship(
    raw: dict[str, Any],
    ctypes: dict[int, dict[str, Any]],
    version: str,
    fetched_at: int,
) -> dict[str, Any]:
    """规整单条 api_mst_ship 为 Ship 模型 dict。

    注：stats 数组字段（api_houg 等）是 [base, max] 形式；
    api_luck 是 [initial_luck, max_luck_with_modernization]。
    """
    ship_id = raw["api_id"]

    # 解析改造后 id（start2 中是字符串，可能为空或 "0"）
    remodel_to: int | None = None
    raw_after = str(raw.get("api_aftershipid") or "").strip()
    if raw_after and raw_after != "0":
        try:
            remodel_to = int(raw_after)
        except ValueError:
            remodel_to = None

    # 解析 stats 数组：取 [0]=base，[1]=max；缺省为 None
    def pick_pair(field: str) -> tuple[int | None, int | None]:
        v = raw.get(field)
        if isinstance(v, list) and len(v) >= 2:
            return _safe_int(v[0]), _safe_int(v[1])
        return None, None

    houg_b, houg_m = pick_pair("api_houg")
    raig_b, raig_m = pick_pair("api_raig")
    tyku_b, tyku_m = pick_pair("api_tyku")
    souk_b, souk_m = pick_pair("api_souk")
    taik_b, taik_m = pick_pair("api_taik")
    luck_b, luck_m = pick_pair("api_luck")

    slot_count = _safe_int(raw.get("api_slot_num"))
    maxeq = raw.get("api_maxeq")
    slot_capacity: list[int] | None = None
    if isinstance(maxeq, list):
        slot_capacity = [_safe_int(x) or 0 for x in maxeq]

    ship_class_id = _safe_int(raw.get("api_ctype"))
    ship_class_jp: str | None = None
    if ship_class_id is not None:
        ctype_entry = ctypes.get(ship_class_id)
        if ctype_entry and "api_name" in ctype_entry:
            ship_class_jp = str(ctype_entry["api_name"])

    # 构建 provenance：本适配器填充的所有字段都标 kcanotify
    prov = {
        f: {"source": "kcanotify", "version": version, "fetched_at": fetched_at}
        for f in (
            "name_jp", "name_romaji", "ship_type_id", "ship_class_id", "ship_class_jp",
            "speed", "range_", "stats_base", "stats_max",
            "remodel_to", "remodel_level", "remodel_fuel_cost", "remodel_ammo_cost",
        )
    }

    return {
        "id": ship_id,
        "name": {"jp": raw.get("api_name"), "romaji": raw.get("api_yomi")},
        "ship_type_id": _safe_int(raw.get("api_stype")),
        "ship_class_id": ship_class_id,
        "ship_class_jp": ship_class_jp,
        "speed": _safe_int(raw.get("api_soku")),
        "range_": _safe_int(raw.get("api_leng")),
        "stats_base": {
            "hp": taik_b, "firepower": houg_b, "torpedo": raig_b,
            "aa": tyku_b, "armor": souk_b, "luck": luck_b,
            "slot_count": slot_count, "slot_capacity": slot_capacity,
            "fuel": _safe_int(raw.get("api_fuel_max")),
            "ammo": _safe_int(raw.get("api_bull_max")),
        },
        "stats_max": {
            "hp": taik_m, "firepower": houg_m, "torpedo": raig_m,
            "aa": tyku_m, "armor": souk_m, "luck": luck_m,
            # slot_count / slot_capacity / fuel / ammo 不随等级变化，不重复存
        },
        "remodel_to": remodel_to,
        "remodel_level": _safe_int(raw.get("api_afterlv")) or None,
        "remodel_fuel_cost": _safe_int(raw.get("api_afterfuel")) or None,
        "remodel_ammo_cost": _safe_int(raw.get("api_afterbull")) or None,
        "provenance": prov,
    }


def _normalize_one_slotitem(
    raw: dict[str, Any],
    version: str,
    fetched_at: int,
) -> dict[str, Any]:
    """规整单条 api_mst_slotitem 为 Equipment 模型 dict。

    字段映射：
    - api_id -> id
    - api_name -> name.jp
    - api_type[2] -> type_icon_id（图标分类）
    - api_type[3] -> type_id（装备类型字典 id）
    - api_houg -> stats.firepower；api_raig -> stats.torpedo
    - api_tyku -> stats.aa；api_souk -> stats.armor
    - api_tais -> stats.asw；api_saku -> stats.los
    - api_houk -> stats.evasion；api_houm -> stats.accuracy
    - api_luck -> stats.luck；api_baku -> stats.bombing
    - api_leng -> range_；api_rare -> rarity
    - api_distance -> distance（飞机半径）
    - api_cost -> cost（LBAS 配置成本）
    - api_broken[4] -> broken（废弃返还 [燃料,弹药,钢,铝]）

    注：装备数值都是单值标量（与 ship 的 [base, max] 数组不同）。
    """
    equip_id = raw["api_id"]

    # api_type 是 5 元素数组，索引 [2] 是图标 id，[3] 是装备类型 id
    api_type = raw.get("api_type", [])
    type_icon_id: int | None = None
    type_id: int | None = None
    if isinstance(api_type, list):
        if len(api_type) > 2:
            type_icon_id = _safe_int(api_type[2])
        if len(api_type) > 3:
            type_id = _safe_int(api_type[3])

    # 废弃返还：4 元素数组 [燃料,弹药,钢,铝]
    api_broken = raw.get("api_broken")
    broken: list[int] | None = None
    if isinstance(api_broken, list) and api_broken:
        broken = [_safe_int(x) or 0 for x in api_broken]

    # provenance：本适配器填充的字段
    equip_fields = (
        "name_jp", "type_icon_id", "type_id", "rarity", "range_",
        "stats", "distance", "cost", "broken",
    )
    prov = {
        f: {"source": "kcanotify", "version": version, "fetched_at": fetched_at}
        for f in equip_fields
    }

    return {
        "id": equip_id,
        "name": {"jp": raw.get("api_name")},
        "type_icon_id": type_icon_id,
        "type_id": type_id,
        "rarity": _safe_int(raw.get("api_rare")),
        "range_": _safe_int(raw.get("api_leng")),
        "stats": {
            "firepower": _safe_int(raw.get("api_houg")),
            "torpedo": _safe_int(raw.get("api_raig")),
            "aa": _safe_int(raw.get("api_tyku")),
            "armor": _safe_int(raw.get("api_souk")),
            "asw": _safe_int(raw.get("api_tais")),
            "los": _safe_int(raw.get("api_saku")),
            "evasion": _safe_int(raw.get("api_houk")),
            "accuracy": _safe_int(raw.get("api_houm")),
            "luck": _safe_int(raw.get("api_luck")),
            "bombing": _safe_int(raw.get("api_baku")),
        },
        "distance": _safe_int(raw.get("api_distance")),
        "cost": _safe_int(raw.get("api_cost")),
        "broken": broken,
        "provenance": prov,
    }


def _safe_int(v: Any) -> int | None:
    """容忍 start2 中字符串/None/负数，统一转 int 或 None。"""
    if v is None:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _maybe_gunzip(body: bytes) -> bytes:
    """kcanotify-gamedata 的 api_start2 实际是 gzip 压缩（无 .gz 扩展名）。

    检测 gzip magic（0x1f 0x8b）决定是否解压。
    """
    if body[:2] == b"\x1f\x8b":
        return gzip.decompress(body)
    return body


def _decode_text(body: bytes) -> str:
    """容忍 gzip 包裹的文本文件。"""
    return _maybe_gunzip(body).decode("utf-8", errors="replace")


# 局部异步 gather 封装（避免顶层 import asyncio 触发循环）
async def _gather(*aws):
    import asyncio
    return await asyncio.gather(*aws)
