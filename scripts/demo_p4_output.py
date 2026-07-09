"""P4 输出 demo：用打包好的 seed 数据演示格式化函数输出。

便于人眼检查输出风格、emoji 密度、对齐效果。
"""
from __future__ import annotations

import asyncio
import gzip
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Windows 控制台默认 GBK，强制 UTF-8 输出（避免 ✓/▸ 等字符报错）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nonebot_plugin_kancolle.commands._format import (  # noqa: E402
    format_basic,
    format_detail,
    format_help_overview,
    format_help_topic,
    format_multiple,
    format_remodel,
)
from nonebot_plugin_kancolle.core.resolver import ShipResolver  # noqa: E402
from nonebot_plugin_kancolle.data.models import ShipEnhancement  # noqa: E402
from nonebot_plugin_kancolle.data.store import Store  # noqa: E402

SEED_GZ = ROOT / "src" / "nonebot_plugin_kancolle" / "seed" / "master.db.gz"
WORK_DB = ROOT / ".tmp" / "demo.db"


def _extract_seed() -> Path:
    WORK_DB.parent.mkdir(parents=True, exist_ok=True)
    if WORK_DB.exists():
        WORK_DB.unlink()
    with gzip.open(SEED_GZ, "rb") as f_in, open(WORK_DB, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    return WORK_DB


def main() -> int:
    db = _extract_seed()
    store = Store(db)
    store.open()
    resolver = ShipResolver(store)

    # 模拟 kcwiki 增强结果（不实际拉网络，避免限流）
    yamato_enh = ShipEnhancement(
        ship_id=131, chinese_name="大和", stype_name_chinese="战舰",
        can_drop=True, wiki_id="131", filename="KanMusu131",
    )

    def show(title: str, text: str) -> None:
        sep = "=" * 60
        print(f"\n{sep}\n【{title}】\n{sep}")
        print(text)

    # 1. 默认卡 - 大和
    yamato = store.get_ship(131)
    if yamato:
        show("查舰娘 大和（默认）", format_basic(yamato, yamato_enh))

    # 2. 详细卡 - 大和
    if yamato:
        show("查舰娘 大和 详细", format_detail(yamato, yamato_enh))

    # 3. 改造链 - 大和
    if yamato:
        show("查舰娘 大和 改造", format_remodel(yamato, store))

    # 4. 多命中 - 输入「Bismarck」可能命中家族
    result = resolver.resolve("Bismarck")
    if result.is_multiple:
        show("查舰娘 Bismarck（多命中）", format_multiple(result))
    elif result.is_single:
        show("查舰娘 Bismarck（single，未触发多命中）",
             f"(本例只命中了 1 艘，未走 multiple 路径)\n\n{format_basic(result.ship)}")

    # 5. 多命中 - 用一个能触发改造链家族的查询
    result2 = resolver.resolve("睦月")
    if result2.is_single:
        # 演示用：手工构造多命中（睦月 + 睦月改）
        mutsuki = store.get_ship(1)
        mutsuki_kai = store.get_ship(254)
        if mutsuki and mutsuki_kai:
            from nonebot_plugin_kancolle.core.result import ResolveResult
            fake_multi = ResolveResult.multiple([mutsuki, mutsuki_kai], hint="chain")
            show("查舰娘 睦月（模拟多命中）", format_multiple(fake_multi))
    elif result2.is_multiple:
        show("查舰娘 睦月（多命中）", format_multiple(result2))

    # 6. 帮助总览
    show("舰C帮助", format_help_overview())

    # 7. 帮助 - 具体指令
    show("舰C帮助 查舰娘", format_help_topic("查舰娘") or "(无)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

