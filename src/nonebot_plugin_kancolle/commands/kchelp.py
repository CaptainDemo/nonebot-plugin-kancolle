"""舰C帮助 / kchelp 指令。

两种用法：
- 舰C帮助              显示所有指令总览
- 舰C帮助 <指令名>     显示具体指令详情

P4 文本输出；未来不改为图片（帮助文本足够紧凑）。
"""
from __future__ import annotations

from arclet.alconna import Alconna, Args
from nonebot_plugin_alconna import Match, on_alconna

from ..utils.limiter import maybe_apply_prefix_variance
from ._format import format_help_overview, format_help_topic

help_cmd = on_alconna(
    Alconna("舰C帮助", Args["topic?", str]),
    aliases={"kchelp"},
    use_cmd_start=True,
    block=False,
    priority=10,
)


@help_cmd.handle()
async def handle_help(topic: Match[str]) -> None:
    """处理帮助查询。"""
    if not topic.available or not topic.result:
        await help_cmd.finish(maybe_apply_prefix_variance(format_help_overview()))
        return

    topic_str = str(topic.result).strip()
    content = format_help_topic(topic_str)
    if content is None:
        await help_cmd.finish(
            maybe_apply_prefix_variance(
                f"未找到指令「{topic_str}」的帮助\n"
                f"可用指令: 查舰娘 / 舰C帮助 / 更新舰娘数据 / 数据状态\n"
                f"输入「舰C帮助」查看总览"
            )
        )
        return

    await help_cmd.finish(maybe_apply_prefix_variance(content))
