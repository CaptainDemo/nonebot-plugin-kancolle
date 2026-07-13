"""插件配置模型。

所有配置项都有默认值，用户在 .env 中可按需覆盖。
配置项前缀统一为 `KANCOLLE_`（nonebot 的 pydantic 配置约定）。

注：本模块只定义 KancolleConfig 类，不在模块顶层调用 get_plugin_config。
原因：get_plugin_config 需要 nonebot runtime 已初始化，模块顶层调用会让
任何 import 本包的代码（包括测试）都要求 runtime 上下文。
调用方通过 get_config() 在需要时获取实际配置值。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field


class KancolleConfig(BaseModel):
    """插件运行配置。"""

    # === 数据源 ===
    kancolle_data_sources: list[str] = Field(
        default_factory=lambda: ["kcanotify", "kc3"],
        description="启用的数据源列表，按字段融合优先级排序。可选: kcanotify / kc3",
    )
    kancolle_data_update_interval_hours: int = Field(
        default=24,
        description="自动检查数据更新的间隔（小时），0 表示禁用自动更新",
        ge=0,
    )
    kancolle_data_update_on_startup: bool = Field(
        default=False,
        description="插件启动时是否自动检查一次更新",
    )
    kancolle_data_github_token: str | None = Field(
        default=None,
        description="GitHub API token，避免匿名限流；不填则匿名访问",
    )

    # === 渲染 ===
    kancolle_render_theme: Literal["dark", "light"] = Field(
        default="dark",
        description="图片渲染主题",
    )
    kancolle_render_cache_ttl_days: int = Field(
        default=30,
        description="图片缓存保留天数",
        ge=1,
    )
    kancolle_render_viewport_width: int = Field(
        default=800,
        description="渲染视窗宽度（像素）",
        ge=400,
        le=1600,
    )

    # === 查询 ===
    kancolle_query_max_list_items: int = Field(
        default=5,
        description="多命中时列表展示的最大条数，超过则仅展示名称",
        ge=1,
        le=20,
    )
    kancolle_query_min_fuzzy_score: int = Field(
        default=60,
        description="rapidfuzz 兜底匹配的最低分数（0-100），低于此分数不视为命中",
        ge=0,
        le=100,
    )

    # === 消息限流（可选集成 nonebot-plugin-message-limiter）===
    kancolle_use_prefix_variance: bool = Field(
        default=False,
        description=(
            "是否对发出去的文本应用随机前缀扰动。"
            "需额外安装 nonebot-plugin-message-limiter；插件缺失时自动降级为原文本。"
        ),
    )


@lru_cache(maxsize=1)
def get_config() -> KancolleConfig:
    """获取实际配置值。首次调用时从 nonebot settings 加载，之后缓存。

    必须在 nonebot runtime 已初始化后调用（如 handler、依赖注入、scheduler job 中）。
    """
    from nonebot import get_plugin_config
    return get_plugin_config(KancolleConfig)

