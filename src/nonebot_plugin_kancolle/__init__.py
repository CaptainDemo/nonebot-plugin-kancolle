"""nonebot-plugin-kancolle 插件入口。

第一阶段（MVP）：仅实现舰娘查询。
"""
# 必须在 import 子模块之前 require 所有依赖插件。
# 原因：htmlrender / localstore / apscheduler / alconna 的 __init__.py
# 顶层会调 require() 反查"调用方插件"，需要我们在 nb 的插件加载上下文里
# 显式声明依赖。否则后续代码 import 它们时会报 "Cannot detect caller plugin"。
from nonebot import require

require("nonebot_plugin_localstore")
require("nonebot_plugin_apscheduler")
require("nonebot_plugin_alconna")
require("nonebot_plugin_htmlrender")

from nonebot.plugin import PluginMetadata

from .config import KancolleConfig

__plugin_meta__ = PluginMetadata(
    name="舰队Collection信息查询",
    description="查询舰娘、装备、任务、海域等信息，支持图片渲染与多源数据融合",
    usage=(
        "=== 舰娘查询 ===\n"
        "查舰娘 <名>          ship <name>           查询舰娘基础卡（图片）\n"
        "查舰娘 <名> 详细     ship <name> -d        查询舰娘详情（多张图片）\n"
        "查舰娘 <名> 改造     ship <name> remodel   查询改造链\n"
        "\n"
        "=== 装备查询 ===\n"
        "查装备 <名>          equip <name>          查询装备基础卡（图片）\n"
        "查装备 <名> 详细     equip <name> -d       查询装备详情（基础+完整数值）\n"
        "\n"
        "=== 帮助 ===\n"
        "舰C帮助              kchelp                查看所有指令\n"
        "舰C帮助 <指令>       kchelp <cmd>          查看某条指令的详细帮助\n"
        "\n"
        "=== 数据管理（仅 SUPERUSER）===\n"
        "更新舰娘数据          kancolle update       立即拉取最新数据\n"
        "数据状态              kancolle status       查看各数据源状态\n"
    ),
    type="application",
    homepage="https://github.com/CaptainDemo/nonebot-plugin-kancolle",
    config=KancolleConfig,
    supported_adapters={"~onebot.v11"},
)

# 注册指令：触发各子模块的 on_alconna() 调用
from . import commands as _commands  # noqa: E402,F401


def _setup_update_scheduler() -> None:
    """启动时注册定时更新与启动触发。

    包在 try/except 里：测试环境（nonebot 未初始化）或 apscheduler 缺失时静默跳过，
    避免影响插件 import。运行时（nonebot 已 init）则正常注册。
    """
    try:
        from .bootstrap import (
            get_adapters,
            get_http_client,
            get_renderer,
            get_store,
        )
        from .config import get_config
        from .update.pipeline import run_update_pipeline
        from .update.scheduler import setup_periodic_update, setup_startup_update

        cfg = get_config()

        async def _runner() -> None:
            store = get_store()
            http = get_http_client()
            try:
                renderer = get_renderer()
            except Exception:
                renderer = None
            try:
                from .bootstrap import get_equipment_renderer
                equip_renderer = get_equipment_renderer()
            except Exception:
                equip_renderer = None
            adapters = get_adapters()
            await run_update_pipeline(store, adapters, http, renderer, equip_renderer)

        setup_periodic_update(cfg.kancolle_data_update_interval_hours, _runner)
        if cfg.kancolle_data_update_on_startup:
            setup_startup_update(_runner)
    except Exception as e:  # noqa: BLE001
        # nonebot 未初始化（测试场景）或 apscheduler 缺失，静默跳过
        from .utils.logger import log
        log.debug(f"update scheduler not registered: {e}")


_setup_update_scheduler()

