"""舰娘立绘懒加载 + 文件缓存。

数据源：kcwiki.cn wiki 的 Special:FilePath 重定向到 uploads.kcwiki.cn 实际文件。
URL 模式：`https://zh.kcwiki.cn/wiki/Special:FilePath/KanMusu{wiki_id}HD.png`

设计要点：
- 与 KcwikiEnhancer 同模式：缓存优先 + per-wiki_id asyncio.Lock 并发去重
- TTL 30 天（立绘极少变化）
- 404 用 .404 标记文件做负缓存（24h，避免反复请求不存在的 id）
- 网络错误不缓存（瞬态失败下次重试）
- 拉到的 PNG 直接落盘，htmlrender 时通过 base64 data URL 内嵌进 HTML
"""
from __future__ import annotations

import asyncio
import base64
import re
import time
from pathlib import Path

import httpx

from ..utils.logger import log

KCWIKI_ART_URL = "https://zh.kcwiki.cn/wiki/Special:FilePath/KanMusu{wiki_id}HD.png"


def _safe_filename(name: str) -> str:
    """文件名安全化（wiki_id 含字母如 031a 也要保留）。"""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", name)


class ShipArtCache:
    """舰娘立绘文件缓存 + 并发去重拉取。

    缓存目录结构：
        <root>/art/ship/<wiki_id>.png    # 正向缓存（PNG 文件）
        <root>/art/ship/<wiki_id>.404    # 负向缓存（空文件，标记 404）
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        cache_root: Path,
        ttl_days: int = 30,
        not_found_ttl_hours: int = 24,
        timeout: float = 15.0,
    ) -> None:
        self._http = http_client
        self._cache_dir = cache_root / "art" / "ship"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_days * 86400
        self._neg_ttl = not_found_ttl_hours * 3600
        self._timeout = timeout
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, wiki_id: str) -> bytes | None:
        """获取立绘 PNG bytes。失败/无 wiki_id/404 都返回 None。"""
        if not wiki_id:
            return None

        hit, data = self._read_cache(wiki_id)
        if hit:
            return data

        # 并发去重：同一 wiki_id 同时只发一个 HTTP
        lock = self._locks.setdefault(wiki_id, asyncio.Lock())
        async with lock:
            # 拿到锁后再查一次（其他协程可能已经填好缓存）
            hit, data = self._read_cache(wiki_id)
            if hit:
                return data
            return await self._fetch_and_cache(wiki_id)

    def _read_cache(self, wiki_id: str) -> tuple[bool, bytes | None]:
        """读缓存。

        返回 (cache_hit, data)：
        - (True, bytes)  正缓存命中，data 是 PNG
        - (True, None)   负缓存命中（404），调用方应直接返回 None
        - (False, None)  未命中或已过期，需要拉取
        """
        png_path = self._cache_dir / f"{_safe_filename(wiki_id)}.png"
        neg_path = self._cache_dir / f"{_safe_filename(wiki_id)}.404"
        now = time.time()

        # 负缓存
        if neg_path.exists():
            if neg_path.stat().st_mtime + self._neg_ttl > now:
                return True, None
            try:
                neg_path.unlink()
            except OSError:
                pass
            return False, None

        # 正缓存
        if png_path.exists():
            if png_path.stat().st_mtime + self._ttl > now:
                try:
                    return True, png_path.read_bytes()
                except OSError as e:
                    log.warning(f"art read failed for {wiki_id}: {e}")
                    return False, None
            # 过期，删除让位给新版本
            try:
                png_path.unlink()
            except OSError:
                pass
        return False, None

    async def _fetch_and_cache(self, wiki_id: str) -> bytes | None:
        """实际发起 HTTP 拉取并写盘。"""
        url = KCWIKI_ART_URL.format(wiki_id=wiki_id)
        try:
            resp = await self._http.get(
                url, timeout=self._timeout, follow_redirects=True
            )
            if resp.status_code == 404:
                self._write_negative(wiki_id)
                log.debug(f"art {wiki_id}: 404, cached negative for {self._neg_ttl//3600}h")
                return None
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.warning(f"art fetch failed for {wiki_id}: {e}")
            return None

        if not resp.content or not resp.content.startswith(b"\x89PNG"):
            log.warning(f"art {wiki_id}: response not PNG ({len(resp.content)} bytes)")
            return None

        path = self._cache_dir / f"{_safe_filename(wiki_id)}.png"
        try:
            path.write_bytes(resp.content)
        except OSError as e:
            log.warning(f"art write failed for {wiki_id}: {e}")
            return None

        log.debug(f"art {wiki_id} cached: {len(resp.content)} bytes")
        return resp.content

    def _write_negative(self, wiki_id: str) -> None:
        """写一个空 .404 标记文件，用作负缓存。"""
        path = self._cache_dir / f"{_safe_filename(wiki_id)}.404"
        try:
            path.touch()
        except OSError as e:
            log.warning(f"art neg-cache write failed for {wiki_id}: {e}")

    def invalidate(self, wiki_id: str | None = None) -> int:
        """失效缓存。指定 wiki_id 时只清该立绘；None 时清空全部。"""
        if wiki_id is None:
            pattern = "*"
        else:
            safe = _safe_filename(wiki_id)
            pattern = f"{safe}.*"
        removed = 0
        for path in self._cache_dir.glob(pattern):
            if path.is_dir():
                continue
            try:
                path.unlink()
                removed += 1
            except OSError as e:
                log.warning(f"art unlink failed for {path}: {e}")
        return removed


def to_data_url(png_bytes: bytes) -> str:
    """PNG bytes 转 base64 data URL，用于 HTML <img src="..."> 内嵌。

    内嵌而非 file:// 是为了避免 playwright 沙箱在 Windows UNC 路径上的权限问题。
    缺点：HTML 体积涨 ~33%（base64 开销），但单张立绘 ~80KB→~110KB 仍可接受。
    """
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"
