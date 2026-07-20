"""KC3Kai-translations 数据源适配器。

源：https://github.com/KC3Kai/kc3-translations
- data/{lang}/ships.json: {JP名: 翻译名} 舰娘名翻译字典
- data/{lang}/items.json: {JP名: 翻译名} 装备名翻译字典（P7）
- data/{lang}/equiptype.json: 5 元素数组，按 api_type[0..4] 维度的装备类型名翻译（P7）
- data/scn/ship_affix.json: 改造后缀/前缀的简中译法（P3 模糊匹配会用）

KC3 是「名翻译」专用源，不提供 stats / 改造链 / 舰种；
本适配器 normalize 出 {id (由 JP 名反查得), name_cn, name_en}。
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from .base import RawData, SourceAdapter
from .github import fetch_latest_commit_sha, fetch_raw

# 启用的语种 -> (KC3 目录, 本适配器输出的 name 字段)
LANG_MAP = {
    "scn": "cn",
    "en": "en",
}


class Kc3TranslationsAdapter(SourceAdapter):
    """KC3Kai translations 适配器，提供多语言舰娘/装备名与装备类型翻译。"""

    name = "kc3"
    REPO = "KC3Kai/kc3-translations"
    REF = "master"

    async def fetch(self, client: httpx.AsyncClient) -> RawData:
        """拉取各语种 ships/items/equiptype.json + ship_affix.json，用 commit_sha 作为版本指纹。"""
        import time

        sha = await fetch_latest_commit_sha(client, self.REPO, self.REF)

        # 并发拉取所需文件
        jobs: list = []
        keys: list[str] = []
        for lang_dir in LANG_MAP:
            jobs.append(fetch_raw(client, self.REPO, f"data/{lang_dir}/ships.json", self.REF))
            keys.append(f"ships_{lang_dir}")
            # 装备名翻译（P7）
            jobs.append(fetch_raw(client, self.REPO, f"data/{lang_dir}/items.json", self.REF))
            keys.append(f"items_{lang_dir}")
            # 装备类型翻译（P7）
            jobs.append(fetch_raw(client, self.REPO, f"data/{lang_dir}/equiptype.json", self.REF))
            keys.append(f"equiptype_{lang_dir}")
        jobs.append(fetch_raw(client, self.REPO, "data/scn/ship_affix.json", self.REF))
        keys.append("affix_scn")
        jobs.append(fetch_raw(client, self.REPO, "data/en/ship_affix.json", self.REF))
        keys.append("affix_en")

        results = await _gather(*jobs)
        files: dict[str, Any] = {}
        for key, res in zip(keys, results, strict=True):
            if res.not_modified or not res.body:
                raise RuntimeError(f"kc3 file {key} returned empty body")
            files[key] = json.loads(res.body)

        return RawData(
            source=self.name,
            version=sha,
            fetched_at=int(time.time()),
            payload={"files": files},
        )

    def normalize_ships(self, raw: RawData) -> Iterator[dict[str, Any]]:
        """输出每个 JP 名对应的 {id: None, name: {cn: ..., en: ...}, provenance: ...}。

        注意：KC3 没有 ship id，输出 id=None；fusion 时由 kcanotify 主数据
        按 JP 名反查合并 name_cn / name_en。
        """
        files = raw.payload.get("files", {})
        scn = files.get("ships_scn", {}) or {}
        en = files.get("ships_en", {}) or {}

        # 仅遍历 scn 字典（覆盖度比 en 略全）；en 中 scn 缺的也补上
        all_jp_names = set(scn.keys()) | set(en.keys())
        for jp_name in all_jp_names:
            if not jp_name:
                continue
            yield {
                "id": None,  # 由 fusion 按 JP 名匹配回填
                "lookup_jp_name": jp_name,  # 临时字段，fusion 用，不进 Ship 模型
                "name": {
                    "cn": scn.get(jp_name),
                    "en": en.get(jp_name),
                },
                "provenance": {
                    "name_cn": {"source": "kc3", "version": raw.version, "fetched_at": raw.fetched_at}
                    if scn.get(jp_name) else {},
                    "name_en": {"source": "kc3", "version": raw.version, "fetched_at": raw.fetched_at}
                    if en.get(jp_name) else {},
                },
            }

    def normalize_slotitems(self, raw: RawData) -> Iterator[dict[str, Any]]:
        """输出每个 JP 名对应的 {id: None, name: {cn: ..., en: ...}, provenance: ...}（装备）。

        与 normalize_ships 同模式：KC3 没有装备 id，输出 id=None；
        fusion 时由 kcanotify 主数据按 JP 名反查合并 name_cn / name_en。
        """
        files = raw.payload.get("files", {})
        scn = files.get("items_scn", {}) or {}
        en = files.get("items_en", {}) or {}

        all_jp_names = set(scn.keys()) | set(en.keys())
        for jp_name in all_jp_names:
            if not jp_name:
                continue
            yield {
                "id": None,
                "lookup_jp_name": jp_name,
                "name": {
                    "cn": scn.get(jp_name),
                    "en": en.get(jp_name),
                },
                "provenance": {
                    "name_cn": {"source": "kc3", "version": raw.version, "fetched_at": raw.fetched_at}
                    if scn.get(jp_name) else {},
                    "name_en": {"source": "kc3", "version": raw.version, "fetched_at": raw.fetched_at}
                    if en.get(jp_name) else {},
                },
            }

    def normalize_equiptypes(self, raw: RawData) -> Iterator[dict[str, Any]]:
        """从 equiptype.json 的索引 [3] 提取类型中英文名。

        equiptype.json 是 5 元素数组（对应 api_type[0..4]），
        每个元素是按类型 id 索引的字符串数组。我们用索引 [3]（最精细分类）。
        缺翻译时输出 None，fusion 由 kcanotify 的 JP 名兜底。
        """
        files = raw.payload.get("files", {})
        scn_arr = files.get("equiptype_scn") or []
        en_arr = files.get("equiptype_en") or []

        # 取索引 [3] 的字符串数组；越界保护
        scn_types: list[str] = scn_arr[3] if isinstance(scn_arr, list) and len(scn_arr) > 3 else []
        en_types: list[str] = en_arr[3] if isinstance(en_arr, list) and len(en_arr) > 3 else []

        # 取两者 type_id 的并集
        all_type_ids = set(range(len(scn_types))) | set(range(len(en_types)))
        for type_id in sorted(all_type_ids):
            cn = scn_types[type_id] if type_id < len(scn_types) else None
            en = en_types[type_id] if type_id < len(en_types) else None
            if not cn and not en:
                continue  # 两者都缺，不输出
            prov: dict[str, dict[str, Any]] = {}
            if cn:
                prov["name_cn"] = {
                    "source": "kc3", "version": raw.version, "fetched_at": raw.fetched_at
                }
            if en:
                prov["name_en"] = {
                    "source": "kc3", "version": raw.version, "fetched_at": raw.fetched_at
                }
            yield {
                "type_id": type_id,
                "name_jp": None,  # 由 fusion 从 kcanotify 兜底
                "name_cn": cn,
                "name_en": en,
                "provenance": prov,
            }

    def priority(self, field: str) -> int:
        """KC3 是 name_cn / name_en 字段的主源（舰娘与装备一致）。"""
        if field in {"name_cn", "name_en"}:
            return 10
        return 1


# 局部异步 gather 封装
async def _gather(*aws):
    import asyncio
    return await asyncio.gather(*aws)
