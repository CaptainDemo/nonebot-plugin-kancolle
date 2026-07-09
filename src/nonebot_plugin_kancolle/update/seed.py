"""首次启动兜底：从包内 seed/master.db.gz 解压。

触发条件：master.db 不存在（首次安装、数据目录被清空）。
作用：保证用户装完插件立即能查询（即使 GitHub 拉取因网络失败）。

seed 数据由 scripts/build_seed.py 离线生成，打包进 wheel。
"""
from __future__ import annotations

import gzip
import shutil
from importlib import resources
from pathlib import Path

from ..utils.logger import log


def seed_db_gz_path() -> Path:
    """返回包内 seed/master.db.gz 路径。"""
    return Path(resources.files("nonebot_plugin_kancolle").joinpath("seed/master.db.gz"))


def seed_exists() -> bool:
    """检查 seed 是否存在于包内。"""
    return seed_db_gz_path().exists()


def extract_seed_if_needed(db_path: Path, force: bool = False) -> bool:
    """若 db_path 不存在，从 seed 解压兜底。

    参数：
        db_path: master.db 目标路径
        force: True 时即使目标已存在也覆盖（用于"重置为 seed"场景）

    返回 True 表示实际解压了；False 表示文件已存在或 seed 不可用。
    """
    if db_path.exists() and not force:
        return False

    gz_path = seed_db_gz_path()
    if not gz_path.exists():
        log.warning(f"seed not found: {gz_path}")
        return False

    db_path.parent.mkdir(parents=True, exist_ok=True)
    # force=True 时先删旧文件（避免 rename 冲突）
    if db_path.exists():
        try:
            db_path.unlink()
        except OSError as e:
            log.warning(f"failed to remove existing db: {e}")
            return False

    try:
        with gzip.open(gz_path, "rb") as f_in, open(db_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    except OSError as e:
        log.error(f"seed extraction failed: {e}")
        # 解压失败时清掉半成品，避免 Store 打开空/坏文件
        if db_path.exists():
            try:
                db_path.unlink()
            except OSError:
                pass
        return False

    log.info(f"seed extracted to {db_path} ({db_path.stat().st_size} bytes)")
    return True
