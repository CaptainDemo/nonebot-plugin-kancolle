"""update/seed.py 单测：seed 兜底解压。"""
from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from nonebot_plugin_kancolle.update.seed import (
    extract_seed_if_needed,
    seed_db_gz_path,
    seed_exists,
)


def _make_fake_seed(gz_path: Path, content: bytes = b"fake sqlite content") -> None:
    """在指定路径写一个假的 .gz（真实 gzip 压缩）。"""
    gz_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(gz_path, "wb") as f:
        f.write(content)


def test_seed_exists_in_package() -> None:
    """打包的 seed/master.db.gz 应该存在（build_seed.py 生成）。"""
    assert seed_exists(), f"expected seed at {seed_db_gz_path()}"


def test_seed_db_gz_path_returns_real_file() -> None:
    p = seed_db_gz_path()
    assert p.exists()
    assert p.name == "master.db.gz"


def test_extract_returns_false_when_db_exists(tmp_path: Path) -> None:
    """db 已存在时不应解压。"""
    db = tmp_path / "master.db"
    db.write_bytes(b"existing data")
    assert extract_seed_if_needed(db) is False
    # 内容没变
    assert db.read_bytes() == b"existing data"


def test_extract_returns_false_when_seed_missing(tmp_path: Path, monkeypatch) -> None:
    """seed 文件不存在时返回 False。"""
    db = tmp_path / "master.db"

    def _fake_path() -> Path:
        return tmp_path / "nonexistent.gz"

    monkeypatch.setattr(
        "nonebot_plugin_kancolle.update.seed.seed_db_gz_path", _fake_path
    )
    monkeypatch.setattr(
        "nonebot_plugin_kancolle.update.seed.seed_exists", lambda: False
    )

    assert extract_seed_if_needed(db) is False
    assert not db.exists()


def test_extract_writes_db_when_missing(tmp_path: Path, monkeypatch) -> None:
    """db 不存在时从 seed 解压。"""
    db = tmp_path / "data" / "master.db"
    gz = tmp_path / "seed" / "master.db.gz"
    _make_fake_seed(gz, content=b"uncompressed seed content")

    monkeypatch.setattr(
        "nonebot_plugin_kancolle.update.seed.seed_db_gz_path", lambda: gz
    )

    assert extract_seed_if_needed(db) is True
    assert db.exists()
    assert db.read_bytes() == b"uncompressed seed content"


def test_extract_force_overwrites_existing(tmp_path: Path, monkeypatch) -> None:
    """force=True 时即使 db 已存在也覆盖。"""
    db = tmp_path / "master.db"
    db.write_bytes(b"old data")

    gz = tmp_path / "master.db.gz"
    _make_fake_seed(gz, content=b"fresh from seed")

    monkeypatch.setattr(
        "nonebot_plugin_kancolle.update.seed.seed_db_gz_path", lambda: gz
    )

    assert extract_seed_if_needed(db, force=True) is True
    assert db.read_bytes() == b"fresh from seed"


def test_extract_creates_parent_dir(tmp_path: Path, monkeypatch) -> None:
    """目标目录不存在时自动创建。"""
    db = tmp_path / "a" / "b" / "c" / "master.db"
    gz = tmp_path / "seed.gz"
    _make_fake_seed(gz)

    monkeypatch.setattr(
        "nonebot_plugin_kancolle.update.seed.seed_db_gz_path", lambda: gz
    )

    assert extract_seed_if_needed(db) is True
    assert db.exists()


def test_extract_real_seed_produces_valid_db(tmp_path: Path) -> None:
    """端到端：用包内真实 seed 解压，验证是合法 SQLite 文件。"""
    db = tmp_path / "from_real_seed.db"
    assert extract_seed_if_needed(db) is True

    # 应该能用 Store 打开（schema 完整）
    from nonebot_plugin_kancolle.data.store import Store
    s = Store(db)
    s.open()
    try:
        # 真实 seed 应该有大量舰娘
        n = s.count_ships()
        assert n > 1000, f"expected >1000 ships in seed, got {n}"
    finally:
        s.close()
