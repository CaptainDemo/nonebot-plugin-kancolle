"""渲染调度器：jinja2 + htmlrender + 图片缓存。

负责：
- 加载 jinja2 模板（包内 templates/ 目录）
- 按 mode 选择 panel 组合（basic → 1 张；detail → 3 张；remodel → 1 张）
- 拉取立绘（按需，通过 ShipArtCache）
- 缓存优先，未命中则渲染 + 写缓存
- htmlrender 调用失败时通过异常信号让 handler 降级到文本

不负责：
- 模板具体样式（在 .html 中）
- 业务数据计算（在 context.py 中）
"""
from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Literal

import jinja2

from ..data.enhancer import KcwikiEnhancer  # noqa: F401 (保留供上层 DI)
from ..data.models import Ship, ShipEnhancement
from ..data.store import Store
from ..utils.logger import log
from . import context as ctx
from .assets import ShipArtCache, to_data_url
from .cache import ImageCache


RenderMode = Literal["basic", "detail", "remodel"]


def _templates_dir() -> Path:
    """返回打包内的 templates 目录路径。"""
    return Path(resources.files("nonebot_plugin_kancolle").joinpath("render/templates"))


def _safe_key(text: str) -> str:
    """把版本指纹等含特殊字符的字符串转为文件名安全的 key。"""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", text)[:200]


class ShipRenderer:
    """舰娘卡片渲染器。

    使用 nonebot_plugin_htmlrender 的 html_to_pic 完成最终的 HTML→PNG。
    htmlrender 不可用（playwright 未装等）时，render() 抛 RenderUnavailable，
    handler 应捕获后降级到文本格式。

    立绘：通过 ShipArtCache 懒加载。enhancement 提供 wiki_id 时才会去拉。
    拉不到（404 / 网络失败 / 无 wiki_id）→ 模板隐藏立绘栏，其他内容正常渲染。
    """

    def __init__(
        self,
        cache_dir: Path,
        default_theme: str = "dark",
        viewport_width: int = 800,
        device_scale_factor: float = 2.0,
        art_cache: ShipArtCache | None = None,
    ) -> None:
        self._cache = ImageCache(cache_dir)
        self._default_theme = default_theme
        self._viewport_width = viewport_width
        self._device_scale_factor = device_scale_factor
        self._art_cache = art_cache  # None 时跳过立绘拉取
        self._templates_dir = _templates_dir()
        self._jinja = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(self._templates_dir)),
            autoescape=jinja2.select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # htmlrender 可用性检测（懒）
        self._htmlrender_checked = False
        self._htmlrender_ok = False

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    async def render(
        self,
        ship: Ship,
        mode: RenderMode,
        data_version: str,
        store: Store,
        enhancement: ShipEnhancement | None = None,
        theme: str | None = None,
    ) -> list[bytes]:
        """渲染指定 mode 的 panel 组合，返回 PNG bytes 列表（按发送顺序）。"""
        actual_theme = theme or self._default_theme

        # 拉立绘（仅 basic 模式需要；detail 的 basic panel 也复用同立绘）
        art_data_url = await self._fetch_art_data_url(enhancement) if (
            mode in ("basic", "detail") and self._art_cache is not None
        ) else None

        panels = self._select_panels(
            mode, ship, store, enhancement, actual_theme, art_data_url
        )
        if not panels:
            raise ValueError(f"no panels for mode={mode}")

        # 渲染前检查 htmlrender 可用性
        if not self._check_htmlrender():
            raise RenderUnavailable("htmlrender/playwright not installed")

        results: list[bytes] = []
        for panel_name, ctx_dict in panels:
            # 立绘存在性影响 layout，需进入 cache key 避免命中错误的布局版本
            has_art = "art_data_url" in ctx_dict and ctx_dict["art_data_url"]
            cache_key = self._cache_key(
                panel_name, ship.id, actual_theme, data_version, has_art=has_art
            )
            png = await self._cache.get(cache_key)
            if png is None:
                html = self._render_html(panel_name, ctx_dict)
                png = await self._call_htmlrender(html)
                if png is None:
                    raise RenderUnavailable(
                        f"htmlrender returned None for panel {panel_name}"
                    )
                await self._cache.set(cache_key, png)
                log.debug(
                    f"rendered panel {panel_name} for ship {ship.id} "
                    f"(cache miss, art={'y' if has_art else 'n'})"
                )
            else:
                log.debug(f"cache hit: {panel_name} ship {ship.id}")
            results.append(png)
        return results

    def invalidate_cache(self, ship_id: int | None = None) -> int:
        """失效缓存。"""
        return self._cache.invalidate(ship_id)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    async def _fetch_art_data_url(
        self, enhancement: ShipEnhancement | None
    ) -> str | None:
        """通过 enhancement.wiki_id 拉立绘，转 base64 data URL；失败返回 None。"""
        if not enhancement or not enhancement.wiki_id or self._art_cache is None:
            return None
        try:
            png = await self._art_cache.get(enhancement.wiki_id)
        except Exception as e:
            log.warning(f"art fetch failed for wiki_id={enhancement.wiki_id}: {e}")
            return None
        if png is None:
            return None
        return to_data_url(png)

    def _select_panels(
        self,
        mode: RenderMode,
        ship: Ship,
        store: Store,
        enhancement: ShipEnhancement | None,
        theme: str,
        art_data_url: str | None = None,
    ) -> list[tuple[str, dict]]:
        """根据 mode 决定渲染哪些 panel 及其 context。"""
        if mode == "basic":
            return [
                ("ship_basic",
                 ctx.build_basic_context(ship, enhancement, theme, art_data_url)),
            ]
        if mode == "detail":
            return [
                ("ship_basic",
                 ctx.build_basic_context(ship, enhancement, theme, art_data_url)),
                ("ship_stats",
                 ctx.build_stats_context(ship, enhancement, theme)),
                ("ship_remodel",
                 ctx.build_remodel_context(ship, store, theme)),
            ]
        if mode == "remodel":
            return [
                ("ship_remodel", ctx.build_remodel_context(ship, store, theme)),
            ]
        raise ValueError(f"unknown render mode: {mode}")

    def _cache_key(
        self,
        panel: str,
        ship_id: int,
        theme: str,
        data_version: str,
        has_art: bool = False,
    ) -> str:
        # has_art 进入 key：避免有/无立绘的两个版本互相覆盖
        art_tag = "a" if has_art else "x"
        return f"{panel}_{ship_id}_{theme}_{art_tag}_{_safe_key(data_version)}"

    def _render_html(self, template_name: str, ctx_dict: dict) -> str:
        tmpl = self._jinja.get_template(f"{template_name}.html")
        return tmpl.render(**ctx_dict)

    async def _call_htmlrender(self, html: str) -> bytes | None:
        """调用 html_to_pic；任何异常都吞掉，返回 None。

        不传 template_path：所有 CSS 内联在 <style> 中，无需相对资源解析。
        """
        try:
            from nonebot_plugin_htmlrender import html_to_pic
        except ImportError as e:
            log.warning(f"htmlrender not importable: {e}")
            return None
        try:
            return await html_to_pic(
                html,
                full_page=True,
                device_scale_factor=self._device_scale_factor,
                viewport={"width": self._viewport_width, "height": 10},
            )
        except Exception as e:
            log.warning(f"html_to_pic failed: {e}")
            return None

    def _check_htmlrender(self) -> bool:
        """检查 htmlrender 是否可正常导入。"""
        if not self._htmlrender_checked:
            try:
                import nonebot_plugin_htmlrender  # type: ignore[import-not-found]  # noqa: F401
                self._htmlrender_ok = True
            except ImportError:
                self._htmlrender_ok = False
            self._htmlrender_checked = True
        return self._htmlrender_ok


class RenderUnavailable(RuntimeError):
    """渲染层不可用（playwright 缺失 / html_to_pic 调用失败）。

    handler 捕获后应降级到 P4 的文本格式输出。
    """
