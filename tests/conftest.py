"""pytest 全局配置。

在 collection 之前 init nonebot，否则 kancolle 的 __init__.py 顶层
``require()`` 链会在 import 阶段抛 ``ValueError: NoneBot has not been
initialized.``，所有需要 import 子模块的测试都会在 collection 时失败。

因此 init 必须放在模块顶层，确保早于任何测试模块的 import。
"""
from __future__ import annotations

import os

# 用最轻量的 driver；不需要 fastapi / websockets
os.environ.setdefault("DRIVER", "~none")
import nonebot  # noqa: E402

try:
    nonebot.get_driver()
except Exception:  # noqa: BLE001
    nonebot.init()
