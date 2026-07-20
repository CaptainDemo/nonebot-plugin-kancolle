"""装备渲染器：jinja2 + htmlrender + 图片缓存（P7）。

职责：
- 加载 jinja2 模板（与 ShipRenderer 共享 templates/ 目录）
- 按 mode 选择 panel 组合（basic → 1 张；detail → 2 张）
- 缓存优先，未命中则渲染 + 写缓存
- htmlrender 不可用时抛 RenderUnavailable，由 handler 降级到文本

不负责：
- 模板具体样式（在 .html 中）
- 业务数据计算（在 context.py 中）
- 装备图标拉取（MVP 不实现，模板预留图标位 placeholder）

与 ShipRenderer 平行独立存在：装备无立绘拉取/无 enhancement，
单一职责，演化方向与舰娘不同（未来装备图标 vs 舰娘季节立绘）。
"""
from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import jinja2

from ..data.models import Equipment, ImprovementData
from ..data.store import Store
from ..utils.logger import log
from . import context as ctx
from .cache import ImageCache

EquipmentRenderMode = Literal["basic", "detail"]


def _templates_dir() -> Path:
    """返回打包内的 templates 目录路径（与 ShipRenderer 共享）。"""
    return Path(resources.files("nonebot_plugin_kancolle").joinpath("render/templates"))


def _safe_key(text: str) -> str:
    """把版本指纹等含特殊字符的字符串转为文件名安全的 key。"""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", text)[:200]


class EquipmentRenderer:
    """装备卡片渲染器。

    使用 nonebot_plugin_htmlrender 的 html_to_pic 完成最终的 HTML→PNG。
    htmlrender 不可用时，render() 抛 RenderUnavailable，handler 应捕获后降级到文本。

    与 ShipRenderer 复用同一 jinja env（templates 目录）+ ImageCache
    （cache_key 加 ``equip_`` 前缀避免冲突）+ htmlrender 探测逻辑。
    """

    def __init__(
        self,
        cache_dir: Path,
        default_theme: str = "dark",
        viewport_width: int = 800,
        device_scale_factor: float = 2.0,
    ) -> None:
        self._cache = ImageCache(cache_dir)
        self._default_theme = default_theme
        self._viewport_width = viewport_width
        self._device_scale_factor = device_scale_factor
        self._templates_dir = _templates_dir()
        self._jinja = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(self._templates_dir)),
            autoescape=jinja2.select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._htmlrender_checked = False
        self._htmlrender_ok = False

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    async def render(
        self,
        equip: Equipment,
        mode: EquipmentRenderMode,
        data_version: str,
        store: Store,
        type_entry: dict[str, object] | None = None,
        theme: str | None = None,
        improvement: ImprovementData | None = None,
        improvement_version: str = "",
    ) -> list[bytes]:
        """渲染指定 mode 的 panel 组合，返回 PNG bytes 列表（按发送顺序）。

        参数：
            improvement: detail 模式第 2 张改修卡所需数据；None 时跳过改修 panel
            improvement_version: 改修数据版本指纹（cache_key 的一部分，避免版本更新命中旧缓存）
        """
        actual_theme = theme or self._default_theme

        panels = self._select_panels(
            mode, equip, store, type_entry, actual_theme, improvement
        )
        if not panels:
            raise ValueError(f"no panels for mode={mode}")

        if not self._check_htmlrender():
            raise RenderUnavailable("htmlrender/playwright not installed")

        results: list[bytes] = []
        for panel_name, ctx_dict in panels:
            cache_key = self._cache_key(
                panel_name, equip.id, actual_theme, data_version,
                improvement_version=improvement_version if panel_name == "equipment_improvement" else "",
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
                log.debug(f"rendered equipment panel {panel_name} for equip {equip.id}")
            else:
                log.debug(f"cache hit: equipment {panel_name} equip {equip.id}")
            results.append(png)
        return results

    def invalidate_cache(self, equip_id: int | None = None) -> int:
        """失效缓存。

        注：当前 ImageCache.invalidate(ship_id) 按 ship 文件名规则匹配。
        装备 cache_key 用 ``equip_`` 前缀，与 ship 区分；不指定 equip_id 时
        全量失效（含舰娘缓存，调用方应理解此行为）。
        """
        if equip_id is None:
            return self._cache.invalidate(None)
        # 按 equip_id 精细失效：扫描缓存目录中匹配前缀的文件
        return self._invalidate_equipment_cache(equip_id)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _select_panels(
        self,
        mode: EquipmentRenderMode,
        equip: Equipment,
        store: Store,
        type_entry: dict[str, object] | None,
        theme: str,
        improvement: ImprovementData | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """根据 mode 决定渲染哪些 panel 及其 context。

        - basic: 仅基础卡（合并后的 basic+stats）
        - detail: 基础卡 + 改修卡（若 improvement 非 None）；improvement 缺失时仅基础卡
        """
        basic_ctx = ("equipment_basic",
                     ctx.build_equipment_basic_context(equip, type_entry, theme))
        if mode == "basic":
            return [basic_ctx]
        if mode == "detail":
            panels = [basic_ctx]
            if improvement is not None:
                panels.append((
                    "equipment_improvement",
                    ctx.build_improvement_context(equip, improvement, theme),
                ))
            return panels
        raise ValueError(f"unknown equipment render mode: {mode}")

    def _cache_key(
        self,
        panel: str,
        equip_id: int,
        theme: str,
        data_version: str,
        improvement_version: str = "",
    ) -> str:
        # equip_ 前缀避免与 ship 缓存冲突；改修卡 key 含 improvement_version
        if panel == "equipment_improvement" and improvement_version:
            return f"equip_{panel}_{equip_id}_{theme}_{_safe_key(improvement_version)}"
        return f"equip_{panel}_{equip_id}_{theme}_{_safe_key(data_version)}"

    def _invalidate_equipment_cache(self, equip_id: int) -> int:
        """按 equip_id 失效装备缓存。返回失效条数。"""
        import contextlib

        cache_dir = self._cache.root
        count = 0
        for f in cache_dir.glob(f"equip_*_{equip_id}_*.png"):
            with contextlib.suppress(OSError):
                f.unlink()
                count += 1
        # 同步失效 .tmp 残留
        for f in cache_dir.glob(f"equip_*_{equip_id}_*.png.tmp"):
            with contextlib.suppress(OSError):
                f.unlink()
        return count

    def _render_html(self, template_name: str, ctx_dict: dict[str, Any]) -> str:
        tmpl = self._jinja.get_template(f"{template_name}.html")
        return tmpl.render(**ctx_dict)

    async def _call_htmlrender(self, html: str) -> bytes | None:
        """调用 html_to_pic；任何异常都吞掉，返回 None。"""
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
            log.warning(f"html_to_pic failed (equipment): {e}")
            return None

    def _check_htmlrender(self) -> bool:
        """检查 htmlrender 是否可正常导入。"""
        if not self._htmlrender_checked:
            try:
                import nonebot_plugin_htmlrender  # noqa: F401
                self._htmlrender_ok = True
            except ImportError:
                self._htmlrender_ok = False
            self._htmlrender_checked = True
        return self._htmlrender_ok


class RenderUnavailable(RuntimeError):  # noqa: N818
    """装备渲染层不可用（playwright 缺失 / html_to_pic 调用失败）。

    命名沿用 ShipRenderer.RenderUnavailable 以保持对称；末尾不加 Error 后缀。
    handler 捕获后应降级到文本格式输出。
    """
