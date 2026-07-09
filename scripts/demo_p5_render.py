"""P5 端到端 demo：解压 seed → 渲染大和基础卡（含立绘） → 写到 .tmp/p5_demo_*.png。

用于人眼检查模板样式。需要 playwright + 已装 chromium + 网络访问 kcwiki.cn。
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

# 必须在 import htmlrender 之前 init nonebot（localstore 模块顶层调用 get_plugin_config）
# 并显式 load_plugin，避免 require() 的"Cannot detect caller plugin"问题（脚本场景）
import os  # noqa: E402
os.environ.setdefault("RENDER_BACKEND", "playwright")

import nonebot  # noqa: E402
nonebot.init(log_level="WARNING", render_backend="playwright")
nonebot.load_plugin("nonebot_plugin_localstore")
nonebot.load_plugin("nonebot_plugin_htmlrender")

import httpx  # noqa: E402
from nonebot_plugin_kancolle.data.models import ShipEnhancement  # noqa: E402
from nonebot_plugin_kancolle.data.store import Store  # noqa: E402
from nonebot_plugin_kancolle.render.assets import ShipArtCache  # noqa: E402
from nonebot_plugin_kancolle.render.renderer import RenderUnavailable, ShipRenderer  # noqa: E402

SEED_GZ = ROOT / "src" / "nonebot_plugin_kancolle" / "seed" / "master.db.gz"
WORK_DB = ROOT / ".tmp" / "p5_demo.db"
OUT_DIR = ROOT / ".tmp" / "p5_demo_out"


async def main() -> int:
    # 解压 seed
    WORK_DB.parent.mkdir(parents=True, exist_ok=True)
    if WORK_DB.exists():
        WORK_DB.unlink()
    with gzip.open(SEED_GZ, "rb") as f_in, open(WORK_DB, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    store = Store(WORK_DB)
    store.open()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 立绘懒加载 cache（共享 httpx 客户端）
    http_client = httpx.AsyncClient(timeout=30.0)
    art_cache = ShipArtCache(http_client, cache_root=OUT_DIR / "cache")

    renderer = ShipRenderer(
        cache_dir=OUT_DIR / "cache",
        default_theme="dark",
        viewport_width=800,
        device_scale_factor=2.0,
        art_cache=art_cache,
    )

    yamato = store.get_ship(131)
    if not yamato:
        print("ERROR: 大和 (id=131) 未在 seed 中找到")
        return 1

    enhancement = ShipEnhancement(
        ship_id=131, chinese_name="大和", stype_name_chinese="战舰",
        can_drop=True, wiki_id="131", filename="KanMusu131",
    )

    data_version = store.get_meta("data_version") or "demo_v1"

    # 清旧缓存，确保本次 demo 重新渲染（带立绘）
    renderer.invalidate_cache()

    for mode in ("basic", "detail", "remodel"):
        try:
            images = await renderer.render(
                yamato, mode, data_version, store, enhancement, theme="dark"
            )
        except RenderUnavailable as e:
            print(f"SKIP mode={mode}: render unavailable ({e})")
            return 2
        except Exception as e:
            print(f"FAIL mode={mode}: {e!r}")
            return 3

        # 保存到磁盘
        for i, img in enumerate(images, 1):
            out = OUT_DIR / f"{mode}_{i}.png"
            out.write_bytes(img)
            print(f"OK  mode={mode:8s}  panel={i}  size={len(img)} bytes  -> {out}")

    await http_client.aclose()
    print("\n所有 panel 已写到:", OUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
