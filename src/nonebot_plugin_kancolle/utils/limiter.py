"""可选集成 nonebot-plugin-message-limiter。

本模块封装对该插件 ``prefix_variance`` 的访问，使上层调用方不必关心：
- 用户是否安装了 ``nonebot-plugin-message-limiter``
- 配置开关 ``kancolle_use_prefix_variance`` 是否启用
- 调用是否抛异常（任意异常都降级为原文本，绝不阻塞消息发送）

设计参考 ``nonebot-plugin-dice-helper``：将 message-limiter 视为可选依赖，
插件缺失时静默降级，保证 kancolle 自身始终可用。

被动限流（``run_preprocessor``）由 message-limiter 在被 bot 加载时自动注册，
kancolle 不必显式 ``require``；此处仅处理「主动文本扰动」一端。
"""
from __future__ import annotations

from typing import Protocol

from ..config import get_config
from .logger import log


class _PrefixVarianceLike(Protocol):
    """prefix_variance.apply 的最小协议，避免硬依赖第三方类型。"""

    def apply(self, message: str, *, separator: str = ...) -> str: ...


_variance: _PrefixVarianceLike | None
try:
    # 模块导入失败 = 用户未安装 message-limiter；不视为错误
    from nonebot_plugin_message_limiter import (
        prefix_variance as _pv,  # type: ignore[import-not-found]
    )

    _variance = _pv  # type: ignore[assignment]
except ImportError:
    _variance = None
except Exception as e:  # noqa: BLE001
    # 其他异常（如插件自身初始化失败）也降级，避免影响 kancolle 启动
    _variance = None
    log.warning(f"prefix_variance 加载失败，前缀扰动功能将不生效: {e}")


def maybe_apply_prefix_variance(text: str) -> str:
    """根据配置与可用性决定是否给文本加随机前缀。

    任何异常都吞掉、返回原文本 —— 消息发送链路绝不应被扰动逻辑阻塞。
    """
    if not text:
        return text

    cfg = get_config()
    if not cfg.kancolle_use_prefix_variance:
        return text

    if _variance is None:
        return text

    try:
        return _variance.apply(text)
    except Exception as e:  # noqa: BLE001
        log.debug(f"prefix_variance.apply failed, returning original text: {e}")
        return text
