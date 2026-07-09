"""图片缓存（文件系统 + aiofiles 异步 IO）。

缓存 key 格式：`{panel}_{ship_id}_{theme}_{data_version}`
对应文件：`{key}.png`

特性：
- 原子写入：先写 .tmp，再 rename，避免半截文件被读到
- 按 ship_id 失效：data_version 升级时，整库清空；改单船时，可按 ship_id 精细失效
- 异步 IO：不阻塞事件循环
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import aiofiles

from ..utils.logger import log


class ImageCache:
    """文件系统图片缓存。线程安全（单实例 + asyncio.Lock 保护写入）。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._write_lock = asyncio.Lock()

    def _path(self, key: str) -> Path:
        return self._root / f"{key}.png"

    async def get(self, key: str) -> bytes | None:
        """读缓存；未命中返回 None。"""
        path = self._path(key)
        if not path.exists():
            return None
        try:
            async with aiofiles.open(path, "rb") as f:
                return await f.read()
        except OSError as e:
            log.warning(f"cache read failed for {key}: {e}")
            return None

    async def set(self, key: str, data: bytes) -> None:
        """写缓存（原子写入）。"""
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        # 写入串行化，避免同一时刻多个协程同时写同一文件
        async with self._write_lock:
            try:
                async with aiofiles.open(tmp, "wb") as f:
                    await f.write(data)
                tmp.replace(path)  # 原子 rename
            except OSError as e:
                log.warning(f"cache write failed for {key}: {e}")
                # 清理临时文件
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass

    def invalidate(self, ship_id: int | None = None) -> int:
        """失效缓存。指定 ship_id 时只清该船的所有 panel；None 时清空全部。"""
        if ship_id is None:
            pattern = "*.png"
        else:
            pattern = f"*_{ship_id}_*.png"
        removed = 0
        for path in self._root.glob(pattern):
            try:
                path.unlink()
                removed += 1
            except OSError as e:
                log.warning(f"cache unlink failed for {path}: {e}")
        if removed:
            log.info(f"cache invalidated: {removed} files (ship_id={ship_id})")
        return removed

    def stats(self) -> dict[str, int]:
        """返回当前缓存统计：文件数 + 总字节数。"""
        files = list(self._root.glob("*.png"))
        total = sum(f.stat().st_size for f in files if f.is_file())
        return {"count": len(files), "bytes": total}
