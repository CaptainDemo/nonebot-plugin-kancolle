"""插件日志器。

复用 nonebot 自带的 loguru，加 [kancolle] 前缀方便过滤。
"""
from nonebot import logger

log = logger.opt(colors=True)
