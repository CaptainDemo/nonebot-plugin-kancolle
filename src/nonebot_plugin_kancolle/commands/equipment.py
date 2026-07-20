"""查装备 / equip 指令（P7）。

两种调用模式：
- 默认：查装备 <名>           → 基础卡（图片，失败降级文本）
- 详细：查装备 <名> 详细 / -d  → 详情（基础卡 + 改修卡，第 2 张缺失时仅基础卡）

无改造模式（装备一般无改造概念）。
渲染失败（playwright 缺失、htmlrender 异常等）自动降级到文本格式。
"""
from __future__ import annotations

from typing import Literal

from arclet.alconna import Alconna, Args, Option, store_true
from nonebot_plugin_alconna import Match, Query, UniMessage, on_alconna

from ..bootstrap import (
    get_equipment_renderer,
    get_equipment_resolver,
    get_improvement_enhancer,
    get_store,
)
from ..data.models import Equipment, ImprovementData
from ..render.equipment_renderer import RenderUnavailable
from ..utils.limiter import maybe_apply_prefix_variance
from ..utils.logger import log
from ._format import (
    format_equipment_basic,
    format_equipment_detail,
    format_equipment_multiple,
)

equip_cmd = on_alconna(
    Alconna(
        "查装备",
        Args["name", str],
        Option("-d|--detail|详细", action=store_true),
    ),
    aliases={"equip", "查询装备"},
    use_cmd_start=True,
    block=False,
    priority=10,
)


@equip_cmd.handle()
async def handle_equipment(
    name: Match[str],
    detail: Query[bool] = Query("detail.value", default=False),  # noqa: B008
) -> None:
    """处理装备查询。"""
    if not name.available or not name.result:
        await equip_cmd.finish(
            maybe_apply_prefix_variance("请在指令后写明装备名，例如「查装备 零式水上偵察機」")
        )
        return

    query = str(name.result).strip()
    log.info(f"equipment query: {query!r}")

    resolver = get_equipment_resolver()
    result = resolver.resolve(query)

    if result.is_none:
        await equip_cmd.finish(
            maybe_apply_prefix_variance(
                result.message or f"未找到与「{query}」匹配的装备"
            )
        )
        return

    if result.is_multiple:
        await equip_cmd.finish(maybe_apply_prefix_variance(format_equipment_multiple(result)))
        return

    # single
    assert result.equipment is not None
    equip = result.equipment

    # 取装备类型条目（用于显示中文名）
    type_entry = None
    if equip.type_id is not None:
        type_entry = get_store().get_equipment_type(equip.type_id)

    # Query 对象默认 truthy，必须显式查 .available + .result
    detail_on = detail.available and bool(detail.result)
    mode: Literal["basic", "detail"] = "detail" if detail_on else "basic"

    # detail 模式：拉取改修数据（懒加载）
    improvement: ImprovementData | None = None
    improvement_version = ""
    if detail_on:
        try:
            enhancer = get_improvement_enhancer()
            improvement = await enhancer.get(equip.id)
            improvement_version = await enhancer.get_version()
        except Exception as e:
            log.warning(f"improvement enhancer failed for {equip.id}: {e}")

    # 优先尝试图片渲染
    images_sent = await _try_send_images(
        equip, mode, type_entry, improvement, improvement_version
    )
    if images_sent:
        return

    # 渲染失败 → 降级到文本
    log.info(f"falling back to text format for equipment {equip.id}")
    if mode == "detail":
        text = format_equipment_detail(equip, type_entry, improvement)
    else:
        text = format_equipment_basic(equip, type_entry)
    await equip_cmd.finish(maybe_apply_prefix_variance(text))


async def _try_send_images(
    equip: Equipment,
    mode: Literal["basic", "detail"],
    type_entry: dict[str, object] | None,
    improvement: ImprovementData | None,
    improvement_version: str,
) -> bool:
    """尝试渲染并发送图片。成功返回 True，失败返回 False。"""
    try:
        store = get_store()
        data_version = store.get_meta("data_version") or "v0"
        renderer = get_equipment_renderer()
        images = await renderer.render(
            equip, mode, data_version, store, type_entry,
            improvement=improvement,
            improvement_version=improvement_version,
        )
    except RenderUnavailable as e:
        log.warning(f"equipment render unavailable: {e}")
        return False
    except Exception as e:
        log.warning(f"equipment render failed unexpectedly: {e}")
        return False

    if not images:
        return False

    try:
        msg = UniMessage.image(raw=images[0])
        for img in images[1:]:
            msg += UniMessage.image(raw=img)
        await msg.send()
    except Exception as e:
        log.warning(f"equipment image send failed: {e}")
        return False

    return True
