"""KC3Kai-translations 数据源适配器。

源：https://github.com/KC3Kai/kc3-translations
- data/scn/ships.json: {JP名: 简中名} 翻译字典
- data/en/ships.json: {JP名: 英文名} 翻译字典
- data/scn/ship_affix.json: 改造后缀/前缀的简中译法（P3 模糊匹配会用）

KC3 是「名翻译」专用源，不提供 stats / 改造链 / 舰种；
本适配器只 normalize 出 {id (由 JP 名反查得), name_cn, name_en}。
"""
from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

from .base import RawData, SourceAdapter
from .github import fetch_raw, fetch_latest_commit_sha


# 启用的语种 -> (KC3 目录, 本适配器输出的 name 字段)
LANG_MAP = {
    "scn": "cn",
    "en": "en",
}


class Kc3TranslationsAdapter(SourceAdapter):
    """KC3Kai translations 适配器，提供多语言舰娘名。"""

    name = "kc3"
    REPO = "KC3Kai/kc3-translations"
    REF = "master"

    async def fetch(self, client: httpx.AsyncClient) -> RawData:
        """拉取各语种 ships.json + ship_affix.json，并用 commit_sha 作为版本指纹。"""
        import time

        sha = await fetch_latest_commit_sha(client, self.REPO, self.REF)

        # 并发拉取所需文件
        jobs = []
        keys: list[str] = []
        for lang_dir in LANG_MAP:
            jobs.append(fetch_raw(client, self.REPO, f"data/{lang_dir}/ships.json", self.REF))
            keys.append(f"ships_{lang_dir}")
        jobs.append(fetch_raw(client, self.REPO, f"data/scn/ship_affix.json", self.REF))
        keys.append("affix_scn")
        jobs.append(fetch_raw(client, self.REPO, f"data/en/ship_affix.json", self.REF))
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

    def priority(self, field: str) -> int:
        """KC3 是 name_cn / name_en 字段的主源。"""
        if field in {"name_cn", "name_en"}:
            return 10
        return 1


# 局部异步 gather 封装
async def _gather(*aws):
    import asyncio
    return await asyncio.gather(*aws)
