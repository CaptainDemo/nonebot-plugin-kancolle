"""Store 单测：sqlite 读写、FTS5 检索、schema 版本管理。"""
from __future__ import annotations

from pathlib import Path

import pytest

from nonebot_plugin_kancolle.data.models import Ship, ShipEnhancement, ShipName, ShipStats
from nonebot_plugin_kancolle.data.store import SCHEMA_VERSION, Store


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.db")
    s.open()
    return s


def _make_ship(id_: int, **overrides) -> Ship:
    """构造测试用 Ship。"""
    defaults = dict(
        id=id_,
        name=ShipName(jp=f"ship_{id_}", cn=f"测试_{id_}", en=f"Ship{id_}"),
        ship_type_id=2,
        stats_base=ShipStats(hp=10, firepower=5),
        stats_max=ShipStats(hp=20, firepower=15),
    )
    defaults.update(overrides)
    return Ship(**defaults)


# ----------------------------------------------------------------------
# 生命周期
# ----------------------------------------------------------------------

def test_open_creates_schema_and_sets_version(tmp_path: Path) -> None:
    s = Store(tmp_path / "x.db")
    s.open()
    try:
        assert s.get_meta("schema_version") == str(SCHEMA_VERSION)
        assert s.count_ships() == 0
    finally:
        s.close()


def test_open_is_idempotent(store: Store) -> None:
    """重复 open 不报错、不丢数据。"""
    store.open()
    store.open()
    assert store.count_ships() == 0


def test_close_then_conn_raises(tmp_path: Path) -> None:
    s = Store(tmp_path / "x.db")
    s.open()
    s.close()
    with pytest.raises(RuntimeError, match="not opened"):
        _ = s.conn


# ----------------------------------------------------------------------
# Ship 读写
# ----------------------------------------------------------------------

def test_write_and_get_ship(store: Store) -> None:
    ship = _make_ship(1)
    n = store.write_ships([ship])
    assert n == 1

    got = store.get_ship(1)
    assert got is not None
    assert got.id == 1
    assert got.name.jp == "ship_1"
    assert got.name.cn == "测试_1"
    assert got.stats_base.hp == 10
    assert got.stats_max.hp == 20


def test_write_ships_upsert(store: Store) -> None:
    """同 id 两次写入应替换而非追加。"""
    store.write_ships([_make_ship(1, name=ShipName(jp="old"))])
    store.write_ships([_make_ship(1, name=ShipName(jp="new"))])
    assert store.count_ships() == 1
    got = store.get_ship(1)
    assert got.name.jp == "new"


def test_get_ship_unknown_returns_none(store: Store) -> None:
    assert store.get_ship(99999) is None


def test_get_ships_by_ids_batch(store: Store) -> None:
    store.write_ships([_make_ship(i) for i in range(1, 6)])
    result = store.get_ships_by_ids([1, 3, 5, 999])  # 999 不存在
    assert set(result.keys()) == {1, 3, 5}


def test_optional_fields_round_trip_with_none(store: Store) -> None:
    """全空可选字段（仅 id + JP 名）也能正确读写。"""
    minimal = Ship(id=42, name=ShipName(jp="只日文"))
    store.write_ships([minimal])
    got = store.get_ship(42)
    assert got is not None
    assert got.id == 42
    assert got.name.jp == "只日文"
    assert got.name.cn is None
    assert got.ship_type_id is None
    assert got.stats_base.hp is None


# ----------------------------------------------------------------------
# FTS5
# ----------------------------------------------------------------------

def test_fts_search_by_jp_name(store: Store) -> None:
    store.write_ships([
        _make_ship(1, name=ShipName(jp="睦月", cn="睦月")),
        _make_ship(2, name=ShipName(jp="大和", cn="大和")),
    ])
    store.rebuild_fts()

    hits = store.search_fts("睦月", limit=5)
    assert len(hits) == 1
    assert hits[0][0] == 1


def test_fts_search_by_cn_name(store: Store) -> None:
    store.write_ships([
        _make_ship(1, name=ShipName(jp="A", cn="睦月", en="A")),
    ])
    store.rebuild_fts()
    hits = store.search_fts("睦月", limit=5)
    assert hits and hits[0][0] == 1


def test_fts_search_empty_query_returns_empty(store: Store) -> None:
    store.write_ships([_make_ship(1)])
    store.rebuild_fts()
    assert store.search_fts("", limit=5) == []
    assert store.search_fts("   ", limit=5) == []


def test_fts_rebuild_clears_old_entries(store: Store) -> None:
    """重建索引后，旧名字不应再被命中。"""
    store.write_ships([_make_ship(1)])
    store.rebuild_fts()
    assert store.search_fts("ship_1", limit=5)

    # 用一艘完全不同的船覆盖（同 id 但名字不同）
    store.write_ships([_make_ship(1, name=ShipName(jp="renamed", cn="改名"))])
    store.rebuild_fts()
    hits = store.search_fts("ship_1", limit=5)
    assert not hits  # 旧名字不应再命中
    hits2 = store.search_fts("renamed", limit=5)
    assert hits2 and hits2[0][0] == 1


# ----------------------------------------------------------------------
# Sources / Conflicts 表
# ----------------------------------------------------------------------

def test_record_and_list_sources(store: Store) -> None:
    store.record_source("kc3", version="abc", fetched_at=100, item_count=500, status="ok")
    store.record_source("kcanotify", version="v1", fetched_at=200, item_count=600, status="ok")
    sources = {s["name"]: s for s in store.list_sources()}
    assert sources["kc3"]["version"] == "abc"
    assert sources["kc3"]["item_count"] == 500
    assert sources["kcanotify"]["status"] == "ok"


def test_record_source_upsert(store: Store) -> None:
    """同名 source 第二次写入应替换。"""
    store.record_source("x", version="v1", fetched_at=1, item_count=0, status="ok")
    store.record_source("x", version="v2", fetched_at=2, item_count=100, status="ok")
    sources = {s["name"]: s for s in store.list_sources()}
    assert len(sources) == 1
    assert sources["x"]["version"] == "v2"
    assert sources["x"]["item_count"] == 100


def test_record_conflict(store: Store) -> None:
    store.record_conflict(
        entity_type="ship",
        entity_id="1",
        field="name_cn",
        winner="kc3",
        candidates=[{"source": "kc3", "value": "睦月"}, {"source": "kcwiki", "value": "睦月"}],
    )
    cur = store.conn.execute("SELECT * FROM conflicts WHERE entity_id = ?", ("1",))
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["winner"] == "kc3"


# ----------------------------------------------------------------------
# 精确名查找（P3 新增）
# ----------------------------------------------------------------------

def test_find_by_exact_name_cn(store: Store) -> None:
    store.write_ships([_make_ship(1, name=ShipName(jp="A", cn="大和", en="Yamato"))])
    found = store.find_by_exact_name("大和")
    assert found is not None
    assert found.id == 1


def test_find_by_exact_name_en_case_insensitive(store: Store) -> None:
    store.write_ships([_make_ship(1, name=ShipName(jp="A", cn="x", en="Yamato"))])
    assert store.find_by_exact_name("yamato") is not None
    assert store.find_by_exact_name("YAMATO") is not None


def test_find_by_exact_name_empty_returns_none(store: Store) -> None:
    store.write_ships([_make_ship(1)])
    assert store.find_by_exact_name("") is None


def test_find_by_exact_name_unknown_returns_none(store: Store) -> None:
    store.write_ships([_make_ship(1)])
    assert store.find_by_exact_name("不存在的名字") is None


# ----------------------------------------------------------------------
# Schema 版本与迁移
# ----------------------------------------------------------------------

def test_schema_version_is_v4() -> None:
    """代码常量与 schema.sql 默认值都是 v4（P7.1 起升级）。"""
    assert SCHEMA_VERSION == 4


def test_open_creates_ship_enhancements_table(store: Store) -> None:
    """v3 库应直接含 ship_enhancements 表。"""
    cur = store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ship_enhancements'"
    )
    assert cur.fetchone() is not None


def test_open_creates_equipment_tables(store: Store) -> None:
    """v4 库应直接含装备相关四张表（含 improvement 缓存）。"""
    for table in (
        "equipments", "equipments_fts", "equipment_types", "equipment_improvements",
    ):
        cur = store.conn.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"
        )
        assert cur.fetchone() is not None, f"缺少表 {table}"


def test_v1_to_v4_migration_applied(tmp_path: Path) -> None:
    """模拟 v1 旧库 → open → 自动迁移到 v4（v1→v2→v3→v4 顺序应用）。"""
    db_path = tmp_path / "legacy.db"
    # 手工建一个 v1 schema（不含 ship_enhancements / equipment_*）
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('schema_version', '1');
        CREATE TABLE ships (id INTEGER PRIMARY KEY);
    """)
    conn.close()

    # 通过 Store 打开，应触发 v1→v2→v3→v4 迁移
    store = Store(db_path)
    store.open()
    try:
        assert store.get_meta("schema_version") == "4"
        for table in (
            "ship_enhancements", "equipments", "equipments_fts",
            "equipment_types", "equipment_improvements",
        ):
            cur = store.conn.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"
            )
            assert cur.fetchone() is not None, f"迁移后缺少表 {table}"
    finally:
        store.close()


def test_v3_to_v4_migration_applied(tmp_path: Path) -> None:
    """模拟 v3 库 → open → 自动迁移到 v4，改修表出现。"""
    db_path = tmp_path / "v3.db"
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('schema_version', '3');
        CREATE TABLE ships (id INTEGER PRIMARY KEY);
        CREATE TABLE ship_enhancements (
            ship_id INTEGER PRIMARY KEY,
            data_json TEXT,
            fetched_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'ok',
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE equipments (id INTEGER PRIMARY KEY);
        CREATE TABLE equipment_types (type_id INTEGER PRIMARY KEY);
    """)
    conn.close()

    store = Store(db_path)
    store.open()
    try:
        assert store.get_meta("schema_version") == "4"
        cur = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='equipment_improvements'"
        )
        assert cur.fetchone() is not None
        # improvement_version meta key 也应被加入
        assert store.get_meta("improvement_version") == ""
    finally:
        store.close()


def test_future_schema_version_rejected(tmp_path: Path) -> None:
    """库的 schema_version 高于代码时拒绝加载，提示升级插件。"""
    db_path = tmp_path / "future.db"
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executescript(f"""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('schema_version', '{SCHEMA_VERSION + 1}');
    """)
    conn.close()

    store = Store(db_path)
    with pytest.raises(RuntimeError, match="upgrade plugin required"):
        store.open()


# ----------------------------------------------------------------------
# ShipEnhancement 读写（P3 新增）
# ----------------------------------------------------------------------

def test_set_and_get_enhancement(store: Store) -> None:
    e = ShipEnhancement(
        ship_id=1, chinese_name="睦月", stype_name_chinese="驱逐舰",
        can_drop=True, wiki_id="031", filename="abc",
    )
    store.set_enhancement(1, e, status="ok", ttl_seconds=86400)

    cached = store.get_enhancement(1)
    assert cached is not None
    data, status, expires = cached
    assert status == "ok"
    assert data is not None
    assert data.ship_id == 1
    assert data.chinese_name == "睦月"
    assert data.can_drop is True
    assert expires > 0


def test_get_enhancement_uncached_returns_none(store: Store) -> None:
    assert store.get_enhancement(99999) is None


def test_set_enhancement_none_data_with_failed_status(store: Store) -> None:
    """status=not_found/failed 时 data=None，get 返回 (None, status, expires)。"""
    store.set_enhancement(2, data=None, status="not_found", ttl_seconds=3600)
    cached = store.get_enhancement(2)
    assert cached is not None
    data, status, _ = cached
    assert status == "not_found"
    assert data is None


def test_cleanup_expired_enhancements(store: Store) -> None:
    """过期的增强记录应被 cleanup 删除。"""
    # 一条已经过期
    store.set_enhancement(1, None, status="not_found", ttl_seconds=-100)
    # 一条未过期
    store.set_enhancement(
        2,
        ShipEnhancement(ship_id=2, chinese_name="x"),
        status="ok",
        ttl_seconds=86400,
    )
    deleted = store.cleanup_expired_enhancements()
    assert deleted == 1
    assert store.get_enhancement(1) is None
    assert store.get_enhancement(2) is not None
