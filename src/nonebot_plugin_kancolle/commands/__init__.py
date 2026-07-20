"""指令注册入口。

nonebot 通过 import 触发 on_alconna() 调用，因此子模块必须在此处被 import
才能注册到 matcher 列表。
"""
from . import (
    admin,  # noqa: F401
    equipment,  # noqa: F401
    kchelp,  # noqa: F401
    ship,  # noqa: F401
)
