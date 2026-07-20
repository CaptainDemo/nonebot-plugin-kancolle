"""生成 seed 数据包（精简版 master.db.gz）。

用途：插件首次安装、用户尚未执行 `更新舰娘数据` 时，从 wheel 内置的 seed 加载
最小可用数据（舰娘 id / 多语言名 / 舰种 / 改造链根 + 装备数据），保证基础查询能力。

P7 起同时跑装备 fusion，seed 中含装备表 + 类型字典。

执行方式（仓库根目录）：
    uv run --no-project --with nonebot2 --with httpx --with pydantic \
        python scripts/build_seed.py

输出：src/nonebot_plugin_kancolle/seed/master.db.gz
"""
from __future__ import annotations

import asyncio
import gzip
import os
import shutil
import sys
from pathlib import Path

# 让 src/ 在 import path 上
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# 初始化 nonebot（最轻量 driver），避免 __init__.py 顶层 require() 失败
os.environ.setdefault("DRIVER", "~none")
import nonebot  # noqa: E402

try:
    nonebot.get_driver()
except Exception:  # noqa: BLE001
    nonebot.init()

import httpx  # noqa: E402

from nonebot_plugin_kancolle.data.fusion import (  # noqa: E402
    _fetch_all_adapters, run_equipment_fusion, run_fusion,
)
from nonebot_plugin_kancolle.data.sources.kc3translations import Kc3TranslationsAdapter  # noqa: E402
from nonebot_plugin_kancolle.data.sources.kcanotify import KcanotifyAdapter  # noqa: E402
from nonebot_plugin_kancolle.data.store import Store  # noqa: E402


SEED_DB_PATH = ROOT / ".tmp" / "seed_master.db"
OUTPUT_GZ = ROOT / "src" / "nonebot_plugin_kancolle" / "seed" / "master.db.gz"


async def build() -> int:
    """跑一次完整 fusion（舰娘 + 装备），把结果作为 seed 打包。"""
    SEED_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SEED_DB_PATH.exists():
        SEED_DB_PATH.unlink()

    store = Store(SEED_DB_PATH)
    store.open()

    adapters = [KcanotifyAdapter(), Kc3TranslationsAdapter()]
    async with httpx.AsyncClient(timeout=60.0) as client:
        # 共享拉取：一次拉两个源的 raws
        raws = await _fetch_all_adapters(adapters, client, store)
        # 舰娘 fusion（复用 raws）
        data_version = await run_fusion(store, adapters, client, raws=raws)
        # 装备 fusion（复用 raws）
        n_equips = await run_equipment_fusion(store, adapters, raws)

    n_ships = store.count_ships()
    store.close()

    # gzip 压缩到目标位置
    OUTPUT_GZ.parent.mkdir(parents=True, exist_ok=True)
    with open(SEED_DB_PATH, "rb") as f_in, gzip.open(OUTPUT_GZ, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    size_kb = OUTPUT_GZ.stat().st_size / 1024
    print(f"seed built: {n_ships} ships, {n_equips} equips, data_version={data_version}")
    print(f"output: {OUTPUT_GZ}  ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(build()))
