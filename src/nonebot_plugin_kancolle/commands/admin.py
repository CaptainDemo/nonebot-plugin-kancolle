"""更新舰娘数据 / kancolle update  +  数据状态 / kancolle status 指令。

权限：
- 更新：仅 SUPERUSER（nonebot.permission.SUPERUSER）
- 状态：所有人

注意：
- 更新指令实际触发 fusion pipeline，耗时 ~10-30s，期间不阻塞 bot
- 状态指令仅查 sources 表，毫秒级返回
"""
from __future__ import annotations

import asyncio
from typing import Any

from arclet.alconna import Alconna
from nonebot.permission import SUPERUSER
from nonebot_plugin_alconna import on_alconna

from ..bootstrap import (
    get_adapters,
    get_equipment_renderer,
    get_http_client,
    get_renderer,
    get_store,
)
from ..update.pipeline import run_update_pipeline
from ..utils.limiter import maybe_apply_prefix_variance
from ..utils.logger import log
from ._format import format_data_status, format_update_result

update_cmd = on_alconna(
    Alconna("更新舰娘数据"),
    aliases={"kancolle update"},
    permission=SUPERUSER,
    use_cmd_start=True,
    block=False,
    priority=10,
)

status_cmd = on_alconna(
    Alconna("数据状态"),
    aliases={"kancolle status"},
    use_cmd_start=True,
    block=False,
    priority=10,
)


@update_cmd.handle()
async def handle_update() -> None:
    """触发数据更新。先发"开始拉取"提示，避免用户以为没响应。"""
    await update_cmd.send(
        maybe_apply_prefix_variance("开始拉取最新数据…（预计 10-30 秒）")
    )

    store = get_store()
    http = get_http_client()
    try:
        renderer = get_renderer()
    except Exception as e:
        # renderer 不可用（playwright 缺失等）也允许更新；只是不清缓存
        log.warning(f"ship renderer unavailable, cache invalidation skipped: {e}")
        renderer = None

    try:
        equip_renderer = get_equipment_renderer()
    except Exception as e:
        log.warning(f"equipment renderer unavailable, cache invalidation skipped: {e}")
        equip_renderer = None

    adapters = get_adapters()
    try:
        result = await asyncio.wait_for(
            run_update_pipeline(store, adapters, http, renderer, equip_renderer),
            timeout=300.0,  # 5 分钟兜底，避免网络卡死 SUPERUSER 的会话
        )
    except TimeoutError:
        await update_cmd.finish(
            maybe_apply_prefix_variance("✗ 更新超时（>5 分钟），请稍后重试")
        )
        return
    except Exception as e:
        log.exception("update command failed")
        await update_cmd.finish(
            maybe_apply_prefix_variance(f"✗ 更新失败：{e}")
        )
        return

    await update_cmd.finish(
        maybe_apply_prefix_variance(format_update_result(result))
    )


@status_cmd.handle()
async def handle_status() -> None:
    """读取 sources 表 + meta，格式化展示。"""
    store = get_store()
    data_version = store.get_meta("data_version") or ""
    ship_count = store.count_ships()
    sources = store.list_sources()

    # list_sources 返回 dict；转换成有属性的简单对象供 format_data_status 用
    class _S:
        def __init__(self, d: dict[str, Any]) -> None:
            self.name = str(d.get("name", "?"))
            self.version = str(d.get("version", "") or "")
            self.status = str(d.get("status", "?"))
            self.fetched_at = int(d.get("fetched_at", 0) or 0)
            self.item_count = int(d.get("item_count", 0) or 0)
            self.error_msg = str(d.get("error_msg", "") or "")

    src_objs: list[object] = [_S(s) for s in sources]
    equip_count = store.count_equipments()
    text = format_data_status(data_version, ship_count, src_objs, equip_count)
    await status_cmd.finish(maybe_apply_prefix_variance(text))
