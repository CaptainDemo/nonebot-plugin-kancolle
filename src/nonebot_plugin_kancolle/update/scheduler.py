"""apscheduler 定时任务注册。

提供两个钩子：
- 周期性更新（按配置间隔小时）
- 启动时触发一次更新（如配置开启）

为兼容测试环境（nonebot 未初始化），本模块只导出注册函数；
实际注册由 main __init__.py 在 try/except 中调用。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from ..utils.logger import log

# 更新任务的工厂签名：返回 awaitable，由 scheduler 调度
PipelineRunner = Callable[[], Awaitable[None]]


def setup_periodic_update(
    interval_hours: int,
    runner: PipelineRunner,
) -> bool:
    """注册周期性更新任务。interval_hours=0 时禁用。

    返回 True 表示注册成功，False 表示 apscheduler 不可用或参数无效。
    """
    if interval_hours <= 0:
        log.info("periodic update disabled (interval_hours=0)")
        return False

    try:
        from nonebot_plugin_apscheduler import scheduler
    except ImportError:
        log.warning("nonebot_plugin_apscheduler not available; periodic update skipped")
        return False

    scheduler.add_job(
        _safe_run(runner, "periodic"),
        "interval",
        hours=interval_hours,
        id="kancolle_data_update_periodic",
        replace_existing=True,
    )
    log.info(f"periodic update registered: every {interval_hours}h")
    return True


def setup_startup_update(
    runner: PipelineRunner,
    delay_seconds: int = 30,
) -> bool:
    """注册启动后单次触发的更新任务。

    delay_seconds=30 给 nonebot / 网络栈一点缓冲时间。
    """
    try:
        from nonebot_plugin_apscheduler import scheduler
    except ImportError:
        log.warning("nonebot_plugin_apscheduler not available; startup update skipped")
        return False

    run_at = datetime.now() + timedelta(seconds=delay_seconds)
    scheduler.add_job(
        _safe_run(runner, "startup"),
        "date",
        run_date=run_at,
        id="kancolle_data_update_on_startup",
        replace_existing=True,
    )
    log.info(f"startup update scheduled at {run_at:%Y-%m-%d %H:%M:%S}")
    return True


def _safe_run(runner: PipelineRunner, label: str) -> Callable[[], Awaitable[None]]:
    """包装 runner，捕获所有异常避免 apscheduler 把进程杀掉。"""

    async def _wrapped() -> None:
        try:
            await runner()
        except Exception:
            log.exception(f"{label} update job failed")

    return _wrapped
