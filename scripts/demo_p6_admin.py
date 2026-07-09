"""P6 端到端 demo：模拟 SUPERUSER 执行「更新舰娘数据」和「数据状态」。

不实际跑 nonebot runtime；直接调用 pipeline + format 函数，展示输出文本。
"""
from __future__ import annotations

import asyncio
import gzip
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import os
os.environ.setdefault("RENDER_BACKEND", "playwright")
import nonebot
nonebot.init(log_level="WARNING", render_backend="playwright")
nonebot.load_plugin("nonebot_plugin_localstore")
nonebot.load_plugin("nonebot_plugin_htmlrender")

import httpx
from nonebot_plugin_kancolle.commands._format import (
    format_data_status,
    format_update_result,
)
from nonebot_plugin_kancolle.data.sources.kc3translations import Kc3TranslationsAdapter
from nonebot_plugin_kancolle.data.sources.kcanotify import KcanotifyAdapter
from nonebot_plugin_kancolle.data.store import Store
from nonebot_plugin_kancolle.update.pipeline import run_update_pipeline
from nonebot_plugin_kancolle.update.seed import extract_seed_if_needed

WORK_DB = ROOT / ".tmp" / "p6_demo.db"


def show(title: str, text: str) -> None:
    sep = "=" * 60
    print(f"\n{sep}\n【{title}】\n{sep}")
    print(text)


async def main() -> int:
    # 1. 先解压 seed，模拟"首次安装"
    if WORK_DB.exists():
        WORK_DB.unlink()
    extracted = extract_seed_if_needed(WORK_DB)
    print(f"seed extracted: {extracted}")

    store = Store(WORK_DB)
    store.open()

    # 2. 模拟「数据状态」查询（基于 seed）
    sources = store.list_sources()
    data_version = store.get_meta("data_version") or ""
    ship_count = store.count_ships()

    class _S:
        def __init__(self, d):
            self.name = str(d.get("name", "?"))
            self.version = str(d.get("version", "") or "")
            self.status = str(d.get("status", "?"))
            self.fetched_at = int(d.get("fetched_at", 0) or 0)
            self.item_count = int(d.get("item_count", 0) or 0)
            self.error_msg = str(d.get("error_msg", "") or "")

    show("数据状态（首启，仅 seed）",
         format_data_status(data_version, ship_count, [_S(s) for s in sources]))

    # 3. 模拟 SUPERUSER 执行「更新舰娘数据」（实际拉网络）
    print("\n拉取最新数据中…")
    async with httpx.AsyncClient(timeout=60.0) as client:
        adapters = [KcanotifyAdapter(), Kc3TranslationsAdapter()]
        result = await run_update_pipeline(store, adapters, client, renderer=None)
    show("更新舰娘数据（SUPERUSER 触发）", format_update_result(result))

    # 4. 再查一次状态
    sources = store.list_sources()
    data_version = store.get_meta("data_version") or ""
    ship_count = store.count_ships()
    show("数据状态（更新后）",
         format_data_status(data_version, ship_count, [_S(s) for s in sources]))

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
