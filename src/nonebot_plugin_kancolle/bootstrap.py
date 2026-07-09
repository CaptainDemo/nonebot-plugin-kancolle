"""插件运行时单例：Store / Resolver / Enhancer / Renderer / ArtCache / httpx client。

懒初始化：首次 getter 调用时才真正 open / 建连，便于：
- 测试环境无需 nonebot runtime 也能 import 本包
- 单元测试可 monkeypatch 这些 getter 返回 mock
- 实际运行时遵循 nonebot_plugin_localstore 提供的数据目录约定
"""
from __future__ import annotations

import httpx

from .core.resolver import ShipResolver
from .data.enhancer import KcwikiEnhancer
from .data.sources.base import SourceAdapter
from .data.sources.kc3translations import Kc3TranslationsAdapter
from .data.sources.kcanotify import KcanotifyAdapter
from .data.store import Store
from .render.assets import ShipArtCache
from .render.renderer import ShipRenderer
from .utils.logger import log

# 模块级单例（首次 getter 调用时填充）
_store: Store | None = None
_http_client: httpx.AsyncClient | None = None
_resolver: ShipResolver | None = None
_enhancer: KcwikiEnhancer | None = None
_renderer: ShipRenderer | None = None
_art_cache: ShipArtCache | None = None
_adapters: list[SourceAdapter] | None = None


def get_store() -> Store:
    """获取 Store 单例。"""
    global _store
    if _store is None:
        from nonebot_plugin_localstore import get_plugin_data_dir
        db_path = get_plugin_data_dir() / "master.db"
        _store = Store(db_path)
        _store.open()
    return _store


def get_http_client() -> httpx.AsyncClient:
    """获取共享 httpx.AsyncClient 单例。"""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


def get_resolver() -> ShipResolver:
    """获取 ShipResolver 单例。"""
    global _resolver
    if _resolver is None:
        from .config import get_config
        cfg = get_config()
        _resolver = ShipResolver(
            get_store(),
            max_list_items=cfg.kancolle_query_max_list_items,
            min_fuzzy_score=cfg.kancolle_query_min_fuzzy_score,
        )
    return _resolver


def get_enhancer() -> KcwikiEnhancer:
    """获取 KcwikiEnhancer 单例。"""
    global _enhancer
    if _enhancer is None:
        _enhancer = KcwikiEnhancer(get_http_client(), get_store())
    return _enhancer


def get_art_cache() -> ShipArtCache:
    """获取 ShipArtCache 单例（舰娘立绘懒加载缓存）。

    TTL 由配置 kancolle_render_cache_ttl_days 控制（默认 30 天）。
    """
    global _art_cache
    if _art_cache is None:
        from .config import get_config
        from nonebot_plugin_localstore import get_plugin_cache_dir
        cfg = get_config()
        _art_cache = ShipArtCache(
            http_client=get_http_client(),
            cache_root=get_plugin_cache_dir(),
            ttl_days=cfg.kancolle_render_cache_ttl_days,
        )
    return _art_cache


def get_renderer() -> ShipRenderer:
    """获取 ShipRenderer 单例。

    配置（主题、视窗宽度）首次调用时从 nonebot settings 加载一次，之后常驻。
    立绘拉取通过 ShipArtCache 单例完成（与 enhancer 同样的懒加载模式）。
    """
    global _renderer
    if _renderer is None:
        from .config import get_config
        from nonebot_plugin_localstore import get_plugin_cache_dir
        cfg = get_config()
        _renderer = ShipRenderer(
            cache_dir=get_plugin_cache_dir() / "render",
            default_theme=cfg.kancolle_render_theme,
            viewport_width=cfg.kancolle_render_viewport_width,
            art_cache=get_art_cache(),
        )
    return _renderer


# 数据源适配器注册表：name → class
_SOURCE_REGISTRY = {
    "kcanotify": KcanotifyAdapter,
    "kc3": Kc3TranslationsAdapter,
}


def get_adapters() -> list[SourceAdapter]:
    """根据配置 kancolle_data_sources 构造启用的适配器列表。

    顺序与配置一致；未知名称 warn 后跳过；全部无效时回退到默认（kcanotify + kc3）。
    """
    global _adapters
    if _adapters is not None:
        return _adapters

    from .config import get_config
    cfg = get_config()
    names = cfg.kancolle_data_sources

    adapters: list[SourceAdapter] = []
    for name in names:
        cls = _SOURCE_REGISTRY.get(name)
        if cls is None:
            log.warning(
                f"unknown source '{name}' in KANCOLLE_DATA_SOURCES; "
                f"valid options: {list(_SOURCE_REGISTRY.keys())}"
            )
            continue
        adapters.append(cls())

    if not adapters:
        log.warning(
            "no valid sources configured; falling back to defaults "
            "(kcanotify + kc3)"
        )
        adapters = [KcanotifyAdapter(), Kc3TranslationsAdapter()]

    _adapters = adapters
    return _adapters


# 测试辅助：允许单测替换单例（避免污染其他测试）
def _reset_singletons() -> None:
    """仅供测试使用：清空所有单例。生产代码勿调用。"""
    global _store, _http_client, _resolver, _enhancer, _renderer, _art_cache, _adapters
    if _store is not None:
        _store.close()
    _store = None
    _http_client = None
    _resolver = None
    _enhancer = None
    _renderer = None
    _art_cache = None
    _adapters = None



