"""SQLite 数据访问层（同步）。

设计要点：
- 使用 stdlib sqlite3，避免引入 aiosqlite 等额外依赖
- 调用方在异步上下文里通过 asyncio.to_thread 调度，避免阻塞事件循环
- 单 Connection 全插件生命周期复用，autocommit 模式
- 暴露类型化的高层 API（write_ships/get_ship/search_ships 等），不暴露 raw cursor
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..utils.logger import log
from ..utils.pinyin import to_pinyin
from .models import Ship, ShipEnhancement, ShipName, ShipStats

SCHEMA_VERSION = 2


def get_schema_sql() -> str:
    """读取打包进 wheel 的 schema.sql 文本。"""
    from importlib import resources
    return resources.files("nonebot_plugin_kancolle").joinpath("schema.sql").read_text(
        encoding="utf-8"
    )


class Store:
    """SQLite 仓库封装。

    线程模型：单连接、check_same_thread=False，但调用方需自行串行化。
    本插件数据访问频率极低（每周一次批量写 + 用户查询时点查），无并发风险。
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def open(self) -> None:
        """打开数据库并初始化 schema。幂等。"""
        if self._conn is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self._db_path,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.executescript("PRAGMA journal_mode = WAL;")
        conn.executescript("PRAGMA foreign_keys = ON;")

        # 检测是否首次创建（meta 表存在 = 已有 schema）
        # 全新库：跑完整 schema.sql；
        # 已有库：仅启用 pragma，schema 升级由 _check_schema_version 中的迁移处理
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        )
        is_fresh = cur.fetchone() is None
        if is_fresh:
            conn.executescript(get_schema_sql())

        self._conn = conn
        self._check_schema_version()
        log.info(f"store opened: {self._db_path} (fresh={is_fresh})")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Store is not opened; call open() first")
        return self._conn

    # ------------------------------------------------------------------
    # Schema 版本管理
    # ------------------------------------------------------------------
    def _check_schema_version(self) -> None:
        cur = self.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'")
        row = cur.fetchone()
        current = int(row["value"]) if row else 0
        if current < SCHEMA_VERSION:
            self._migrate(current, SCHEMA_VERSION)
        elif current > SCHEMA_VERSION:
            raise RuntimeError(
                f"DB schema_version {current} higher than code {SCHEMA_VERSION}; "
                f"upgrade plugin required"
            )

    def _migrate(self, from_v: int, to_v: int) -> None:
        """应用 schema 迁移。按版本阶梯顺序应用每个 _migrate_vN_to_v(N+1)。"""
        log.info(f"migrating schema from {from_v} to {to_v}")
        cur = from_v
        while cur < to_v:
            next_v = cur + 1
            method = getattr(self, f"_migrate_v{cur}_to_v{next_v}", None)
            if method is None:
                raise RuntimeError(f"no migration path from v{cur} to v{next_v}")
            method()
            cur = next_v
        self.conn.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'",
            (str(to_v),),
        )

    def _migrate_v1_to_v2(self) -> None:
        """v1 → v2: 添加 ship_enhancements 表（kcwiki 懒加载缓存）。"""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ship_enhancements (
                ship_id    INTEGER PRIMARY KEY,
                data_json  TEXT,
                fetched_at INTEGER NOT NULL,
                status     TEXT NOT NULL DEFAULT 'ok',
                expires_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_enhancements_expires
                ON ship_enhancements(expires_at);
            """
        )
        log.info("applied v1->v2: ship_enhancements table created")

    # ------------------------------------------------------------------
    # Ship 读写
    # ------------------------------------------------------------------
    def write_ships(self, ships: list[Ship]) -> int:
        """批量 upsert 舰娘记录。返回写入条数。"""
        rows = [self._ship_to_row(s) for s in ships]
        with self.conn:  # 显式事务（autocommit 模式下 with 仍能管理 transaction）
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO ships (
                    id, name_jp, name_cn, name_en, romaji, aliases_json,
                    ship_type_id, ship_class_id, ship_class_jp, rarity, speed, range_,
                    stats_base_json, stats_max_json,
                    remodel_to, remodel_level, remodel_fuel_cost, remodel_ammo_cost,
                    remodel_from, remodel_chain_root,
                    provenance_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def get_ship(self, ship_id: int) -> Ship | None:
        """按 id 取单条舰娘。"""
        cur = self.conn.execute("SELECT * FROM ships WHERE id = ?", (ship_id,))
        row = cur.fetchone()
        return self._row_to_ship(row) if row else None

    def get_ships_by_ids(self, ids: list[int]) -> dict[int, Ship]:
        """按 id 批量取，返回 {id: Ship}。"""
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        cur = self.conn.execute(
            f"SELECT * FROM ships WHERE id IN ({placeholders})", ids
        )
        return {row["id"]: self._row_to_ship(row) for row in cur.fetchall()}

    def all_ships(self) -> list[Ship]:
        """全表扫描（仅用于 fusion 前的旧数据查看或测试）。"""
        cur = self.conn.execute("SELECT * FROM ships ORDER BY id")
        return [self._row_to_ship(row) for row in cur.fetchall()]

    def find_by_exact_name(self, name: str) -> Ship | None:
        """按精确名匹配（jp / cn / en / romaji 任一命中即可）。

        大小写不敏感；空字符串永远不命中。供 ShipResolver 第一步使用。
        """
        if not name:
            return None
        cur = self.conn.execute(
            """
            SELECT * FROM ships
            WHERE name_jp = ? OR name_cn = ? OR name_en = ? OR romaji = ?
               OR LOWER(name_jp) = LOWER(?)
               OR LOWER(name_cn) = LOWER(?)
               OR LOWER(name_en) = LOWER(?)
            LIMIT 1
            """,
            (name, name, name, name, name, name, name),
        )
        row = cur.fetchone()
        return self._row_to_ship(row) if row else None

    def count_ships(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) AS n FROM ships")
        return int(cur.fetchone()["n"])

    # ------------------------------------------------------------------
    # FTS5 索引
    # ------------------------------------------------------------------
    def rebuild_fts(self) -> int:
        """重建全文索引。返回索引行数。

        contentless 模式（P3 改造后）：DELETE + INSERT 全表。
        拼音列由本方法现场计算（中文 cn 名 → 无声调拼音），供中文拼音匹配。
        """
        with self.conn:
            self.conn.execute("DELETE FROM ships_fts")
            rows = self.conn.execute(
                "SELECT id, name_jp, name_cn, name_en, romaji, aliases_json FROM ships"
            ).fetchall()

        count = 0
        for row in rows:
            aliases = json.loads(row["aliases_json"] or "[]")
            aliases_text = " ".join(aliases)
            # 中文名 → 拼音，方便「dahe」匹配「大和」之类
            pinyin_text = to_pinyin(row["name_cn"] or "")
            self.conn.execute(
                """
                INSERT INTO ships_fts
                    (ship_id, name_jp, name_cn, name_en, romaji, pinyin, aliases)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["name_jp"] or "",
                    row["name_cn"] or "",
                    row["name_en"] or "",
                    row["romaji"] or "",
                    pinyin_text,
                    aliases_text,
                ),
            )
            count += 1
        log.info(f"fts rebuilt: {count} rows")
        return count

    def search_fts(self, query: str, limit: int = 20) -> list[tuple[int, int]]:
        """全文检索。

        返回 [(ship_id, rank_score)]，按 FTS5 bm25 相关度排序。
        query 直接交由 FTS5 处理（支持 OR/AND/前缀 * 等）。
        """
        if not query.strip():
            return []
        sql = (
            "SELECT ship_id, bm25(ships_fts) AS score "
            "FROM ships_fts "
            "WHERE ships_fts MATCH ? "
            "ORDER BY score LIMIT ?"
        )
        cur = self.conn.execute(sql, (query, limit))
        # bm25 返回负数（越小越相关）；转换为正分数便于上层使用
        return [(int(r["ship_id"]), -int(r["score"] * 1000)) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # 数据源元信息
    # ------------------------------------------------------------------
    def record_source(
        self,
        name: str,
        version: str,
        fetched_at: int,
        item_count: int,
        status: str,
        error_msg: str = "",
    ) -> None:
        """upsert sources 表一条记录。"""
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO sources
                    (name, version, fetched_at, item_count, status, error_msg)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, version, fetched_at, item_count, status, error_msg),
            )

    def list_sources(self) -> list[dict[str, object]]:
        cur = self.conn.execute(
            "SELECT name, version, fetched_at, item_count, status, error_msg FROM sources"
        )
        return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Meta 工具
    # ------------------------------------------------------------------
    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, value),
            )

    def get_meta(self, key: str) -> str | None:
        cur = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else None

    def record_conflict(
        self,
        entity_type: str,
        entity_id: str,
        field: str,
        winner: str,
        candidates: list[dict[str, object]],
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO conflicts (entity_type, entity_id, field, winner, candidates_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (entity_type, entity_id, field, winner, json.dumps(candidates, ensure_ascii=False)),
            )

    # ------------------------------------------------------------------
    # ShipEnhancement（kcwiki 懒加载缓存）
    # ------------------------------------------------------------------
    def get_enhancement(self, ship_id: int) -> tuple[ShipEnhancement | None, str, int] | None:
        """取增强缓存。返回 (data, status, expires_at)；未缓存返回 None。

        status: 'ok' / 'failed' / 'not_found'。data 仅在 status='ok' 时有值。
        调用方需自行判断 expires_at 是否过期。
        """
        cur = self.conn.execute(
            "SELECT data_json, status, expires_at FROM ship_enhancements WHERE ship_id = ?",
            (ship_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        data: ShipEnhancement | None = None
        if row["status"] == "ok" and row["data_json"]:
            data = ShipEnhancement.model_validate_json(row["data_json"])
        return data, row["status"], int(row["expires_at"])

    def set_enhancement(
        self,
        ship_id: int,
        data: ShipEnhancement | None,
        status: str,
        ttl_seconds: int,
    ) -> None:
        """写入/更新增强缓存。data 为 None 时 data_json 留空。"""
        data_json = data.model_dump_json() if data else ""
        now = int(time.time())
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO ship_enhancements
                    (ship_id, data_json, fetched_at, status, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ship_id, data_json, now, status, now + ttl_seconds),
            )

    def cleanup_expired_enhancements(self) -> int:
        """删除已过期的增强缓存（避免无限增长）。返回删除条数。"""
        now = int(time.time())
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM ship_enhancements WHERE expires_at < ?", (now,)
            )
        return cur.rowcount or 0

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    @staticmethod
    def _ship_to_row(s: Ship) -> tuple[object, ...]:
        return (
            s.id,
            s.name.jp,
            s.name.cn,
            s.name.en,
            s.name.romaji,
            json.dumps(s.aliases, ensure_ascii=False),
            s.ship_type_id,
            s.ship_class_id,
            s.ship_class_jp,
            s.rarity,
            s.speed,
            s.range_,
            s.stats_base.model_dump_json(),
            s.stats_max.model_dump_json(),
            s.remodel_to,
            s.remodel_level,
            s.remodel_fuel_cost,
            s.remodel_ammo_cost,
            s.remodel_from,
            s.remodel_chain_root,
            json.dumps(s.provenance, ensure_ascii=False),
            int(time.time()),
        )

    @staticmethod
    def _row_to_ship(row: sqlite3.Row) -> Ship:
        return Ship(
            id=int(row["id"]),
            name=ShipName(
                jp=row["name_jp"],
                cn=row["name_cn"],
                en=row["name_en"],
                romaji=row["romaji"],
            ),
            aliases=json.loads(row["aliases_json"] or "[]"),
            ship_type_id=_int(row["ship_type_id"]),
            ship_class_id=_int(row["ship_class_id"]),
            ship_class_jp=row["ship_class_jp"],
            rarity=_int(row["rarity"]),
            speed=_int(row["speed"]),
            range_=_int(row["range_"]),
            stats_base=ShipStats.model_validate_json(row["stats_base_json"] or "{}"),
            stats_max=ShipStats.model_validate_json(row["stats_max_json"] or "{}"),
            remodel_to=_int(row["remodel_to"]),
            remodel_level=_int(row["remodel_level"]),
            remodel_fuel_cost=_int(row["remodel_fuel_cost"]),
            remodel_ammo_cost=_int(row["remodel_ammo_cost"]),
            remodel_from=_int(row["remodel_from"]),
            remodel_chain_root=_int(row["remodel_chain_root"]),
            provenance=json.loads(row["provenance_json"] or "{}"),
        )


def _int(v: object | None) -> int | None:
    """sqlite Row 字段可能是 None 或 int；统一转 Optional[int]。"""
    if v is None:
        return None
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def init_db(db_path: Path) -> Store:
    """便利函数：打开并初始化数据库，返回 Store 实例。

    异步签名仅为 API 一致性；实际是同步操作（毫秒级）。
    """
    store = Store(db_path)
    store.open()
    return store
