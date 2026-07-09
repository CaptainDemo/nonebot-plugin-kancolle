"""查舰娘 / ship 指令。

三种调用模式：
- 默认：查舰娘 <名>           → 基础卡（P5 图片，失败降级文本）
- 详细：查舰娘 <名> 详细 / -d  → 详情（基础 + 数值 + 改造链 三张图）
- 改造：查舰娘 <名> 改造       → 改造链（一张图）

P5 起：single + basic/detail/remodel 走图片；multiple / none 保持 P4 文本。
渲染失败（playwright 缺失、htmlrender 异常等）自动降级到 P4 文本格式，
保证可用性。
"""
from __future__ import annotations

from arclet.alconna import Alconna, Args, Option, store_true
from nonebot_plugin_alconna import Match, Query, UniMessage, on_alconna

from ..bootstrap import get_enhancer, get_renderer, get_resolver, get_store
from ..data.models import ShipEnhancement
from ..render.renderer import RenderUnavailable
from ..utils.logger import log
from ._format import format_basic, format_detail, format_multiple, format_remodel

ship_cmd = on_alconna(
    Alconna(
        "查舰娘",
        Args["name", str],
        Option("-d|--detail|详细", action=store_true),
        Option("remodel|改造", action=store_true),
    ),
    aliases={"ship", "查询舰娘"},
    use_cmd_start=True,
    block=False,
    priority=10,
)


@ship_cmd.handle()
async def handle_ship(
    name: Match[str],
    detail: Query[bool] = Query("detail.value", default=False),
    remodel: Query[bool] = Query("remodel.value", default=False),
) -> None:
    """处理舰娘查询。"""
    if not name.available or not name.result:
        await ship_cmd.finish("请在指令后写明舰娘名，例如「查舰娘 大和」")
        return

    query = str(name.result).strip()
    log.info(f"ship query: {query!r}")

    resolver = get_resolver()
    result = resolver.resolve(query)

    if result.is_none:
        await ship_cmd.finish(result.message or f"未找到与「{query}」匹配的舰娘")
        return

    if result.is_multiple:
        await ship_cmd.finish(format_multiple(result))
        return

    # single
    assert result.ship is not None
    ship = result.ship

    # 拉取 kcwiki 增强（懒加载；失败优雅降级）
    enhancement: ShipEnhancement | None = None
    try:
        enhancement = await get_enhancer().get(ship.id)
    except Exception as e:
        log.warning(f"enhancer failed for {ship.id}: {e}")

    # Query 对象默认 truthy（无 __bool__），必须显式查 .available + .result
    # 否则任何 Query 实例都会让 if 为真，导致 mode 永远是 "remodel"
    remodel_on = remodel.available and bool(remodel.result)
    detail_on = detail.available and bool(detail.result)
    mode = "remodel" if remodel_on else ("detail" if detail_on else "basic")

    # 优先尝试图片渲染
    images_sent = await _try_send_images(ship, mode, enhancement)
    if images_sent:
        return  # 渲染并发送成功，结束 handler

    # 渲染失败 → 降级到 P4 文本
    log.info(f"falling back to text format for ship {ship.id}")
    if mode == "remodel":
        text = format_remodel(ship, get_store())
    elif mode == "detail":
        text = format_detail(ship, enhancement)
    else:
        text = format_basic(ship, enhancement)
    await ship_cmd.finish(text)


async def _try_send_images(
    ship: object, mode: str, enhancement: ShipEnhancement | None
) -> bool:
    """尝试渲染并发送图片。成功返回 True，失败返回 False。"""
    try:
        store = get_store()
        data_version = store.get_meta("data_version") or "v0"
        renderer = get_renderer()
        images = await renderer.render(ship, mode, data_version, store, enhancement)  # type: ignore[arg-type]
    except RenderUnavailable as e:
        log.warning(f"render unavailable: {e}")
        return False
    except Exception as e:
        log.warning(f"render failed unexpectedly: {e}")
        return False

    if not images:
        return False

    try:
        # 多张图合并为一条消息（OneBot v11 / NapCat 支持批量发送）
        msg = UniMessage.image(raw=images[0])
        for img in images[1:]:
            msg += UniMessage.image(raw=img)
        await msg.send()
    except Exception as e:
        log.warning(f"image send failed: {e}")
        return False

    return True
