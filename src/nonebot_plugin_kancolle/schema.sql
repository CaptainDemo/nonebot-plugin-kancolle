-- ============================================================================
-- nonebot-plugin-kancolle 数据库 Schema
-- Schema 版本：1
-- ----------------------------------------------------------------------------
-- 设计原则：
-- 1. 所有字段（除主键）都允许 NULL —— 保证「旧数据 + 新代码」向前兼容
-- 2. 复杂嵌套结构走 JSON 字段（stats、provenance 等）
-- 3. 多语言名 / 别名 / 拼音 同时入主表与 FTS5 索引，便于不同匹配路径
-- ============================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------------------
-- meta: 元信息表
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 首次建库时写入的元信息（INSERT OR IGNORE 保证幂等）
INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '4');
INSERT OR IGNORE INTO meta (key, value) VALUES ('created_at', strftime('%s', 'now'));
INSERT OR IGNORE INTO meta (key, value) VALUES ('data_version', '');
-- improvement_version: kcwiki-improvement-data 仓库 gh-pages 分支的 commit_sha，
-- 由 ImprovementEnhancer 首次拉取后写入。作为改修卡渲染缓存键的一部分。
INSERT OR IGNORE INTO meta (key, value) VALUES ('improvement_version', '');
-- data_version 由 P2 fusion 完成后写入（取所有源 commit_sha 的拼接指纹），
-- 用于触发渲染缓存失效。

-- ----------------------------------------------------------------------------
-- ships: 舰娘主表
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ships (
    id                 INTEGER PRIMARY KEY,
    name_jp            TEXT,
    name_cn            TEXT,
    name_en            TEXT,
    romaji             TEXT,
    aliases_json       TEXT DEFAULT '[]',   -- list[str]
    ship_type_id       INTEGER,             -- stype id（2=DD 等）
    ship_class_id      INTEGER,             -- ctype id
    ship_class_jp      TEXT,                -- 舰级 JP 名（如「睦月型」）
    rarity             INTEGER,             -- 1-7（start2 主数据无，恒为 NULL）
    speed              INTEGER,             -- 5/10/15/20
    range_             INTEGER,             -- 1/2/3/4
    stats_base_json    TEXT DEFAULT '{}',   -- ShipStats 序列化（初期值）
    stats_max_json     TEXT DEFAULT '{}',   -- ShipStats 序列化（满级值）
    remodel_to         INTEGER,             -- 改造后形态的 ship_id
    remodel_level      INTEGER,             -- 改造所需等级
    remodel_fuel_cost  INTEGER,             -- 改造消耗燃料
    remodel_ammo_cost  INTEGER,             -- 改造消耗弹药
    remodel_from       INTEGER,             -- 改造前形态的 ship_id（fusion 反向回溯写入）
    remodel_chain_root INTEGER,             -- 改造链起点的 ship_id（用于反查同链）
    provenance_json    TEXT DEFAULT '{}',   -- 字段来源追溯 {field: {source, version}}
    updated_at         INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_ships_chain_root ON ships(remodel_chain_root);
CREATE INDEX IF NOT EXISTS idx_ships_type       ON ships(ship_type_id);
CREATE INDEX IF NOT EXISTS idx_ships_name_cn    ON ships(name_cn);
CREATE INDEX IF NOT EXISTS idx_ships_name_jp    ON ships(name_jp);

-- ----------------------------------------------------------------------------
-- ships_fts: 舰娘全文检索（FTS5，contentless）
-- 采用 contentless 模式（不绑定外部 content 表），由应用层手动维护索引。
-- 优点：DELETE/INSERT 不受外部表列名约束；缺点：需要应用层显式重建。
-- 本插件数据更新频率低（每周），rebuild 一次完全可接受。
-- tokenize 用 unicode61，对中日韩字符按字切分，配合 trigram 兜底模糊。
-- ----------------------------------------------------------------------------
CREATE VIRTUAL TABLE IF NOT EXISTS ships_fts USING fts5(
    ship_id UNINDEXED,
    name_jp,
    name_cn,
    name_en,
    romaji,
    pinyin,
    aliases,
    tokenize='unicode61'
);

-- ----------------------------------------------------------------------------
-- aliases: 手动别名表（最高优先级，覆盖自动别名）
-- 用于社区惯用名、常见错字等。entity_type 区分 ship/equipment/quest/map。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aliases (
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    alias       TEXT NOT NULL,
    source      TEXT,
    PRIMARY KEY (entity_type, entity_id, alias)
);

CREATE INDEX IF NOT EXISTS idx_aliases_text ON aliases(alias);

-- ----------------------------------------------------------------------------
-- sources: 数据源元信息（数据状态指令直接读这张表）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    name        TEXT PRIMARY KEY,
    version     TEXT,                  -- 上游版本（commit_sha / tag / DATA_VERSION）
    fetched_at  INTEGER,               -- 最近一次抓取时间（unix 秒）
    item_count  INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'pending', -- pending / ok / failed / stale
    error_msg   TEXT
);

-- ----------------------------------------------------------------------------
-- conflicts: 融合冲突日志（debug 用）
-- 当 fusion 检测到不同源对同一字段给出不同值时记录，便于排查数据问题。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conflicts (
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    field           TEXT NOT NULL,
    winner          TEXT,              -- 最终采纳的源
    candidates_json TEXT,              -- [{source, value}, ...]
    at              INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_conflicts_entity ON conflicts(entity_type, entity_id);

-- ----------------------------------------------------------------------------
-- ship_enhancements: kcwiki 懒加载增强数据缓存（schema v2 引入）
-- ----------------------------------------------------------------------------
-- 用户查询某舰娘时，按需从 api.kcwiki.moe/ship/{id} 拉取增量字段
--（can_drop / wiki_id / 舰种中文名 等），缓存到这张表。
-- status: ok=有数据 / failed=404 或网络失败（避免反复请求）/ not_found=kcwiki 无此 id
CREATE TABLE IF NOT EXISTS ship_enhancements (
    ship_id    INTEGER PRIMARY KEY,
    data_json  TEXT,                     -- ShipEnhancement 序列化
    fetched_at INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'ok',
    expires_at INTEGER NOT NULL          -- fetched_at + TTL，过期重新拉取
);

CREATE INDEX IF NOT EXISTS idx_enhancements_expires ON ship_enhancements(expires_at);

-- ----------------------------------------------------------------------------
-- equipment_types: 装备类型字典（schema v3 引入，P7 装备查询）
-- ----------------------------------------------------------------------------
-- 来自 start2 api_mst_slotitem_equiptype（api_id/api_name）+ kc3-translations
-- equiptype.json 索引 [3]（中英文名）。共 62 条。
-- type_id 对应 equipment.type_id（即 api_type[3]，最精细分类）。
CREATE TABLE IF NOT EXISTS equipment_types (
    type_id    INTEGER PRIMARY KEY,
    name_jp    TEXT,
    name_cn    TEXT,
    name_en    TEXT,
    updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- ----------------------------------------------------------------------------
-- equipments: 装备主表（schema v3 引入）
-- ----------------------------------------------------------------------------
-- 来自 start2 api_mst_slotitem（729 条）+ kc3-translations items.json（中英文名）。
-- type_id 关联 equipment_types；type_icon_id 是 api_type[2]，用于图标分类。
-- stats 是单值（与 ships 的 base/max 双值不同），用单个 JSON 字段。
-- distance/cost 仅飞机有；broken 是 4 元素数组 [燃料,弹药,钢,铝]。
CREATE TABLE IF NOT EXISTS equipments (
    id              INTEGER PRIMARY KEY,
    name_jp         TEXT,
    name_cn         TEXT,
    name_en         TEXT,
    aliases_json    TEXT DEFAULT '[]',
    type_icon_id    INTEGER,              -- api_type[2]，图标分类
    type_id         INTEGER,              -- api_type[3]，装备类型 id
    rarity          INTEGER,              -- 0-7
    range_          INTEGER,              -- 0=无/1=短/2=中/3=长/4=超长/5=超超长
    stats_json      TEXT DEFAULT '{}',    -- EquipmentStats 序列化（单值）
    distance        INTEGER,              -- 飞机半径（仅飞机类有）
    cost            INTEGER,              -- LBAS 配置成本（仅飞机类有）
    broken_json     TEXT,                 -- [燃料,弹药,钢,铝]，可为 NULL
    provenance_json TEXT DEFAULT '{}',
    updated_at      INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_equipments_type     ON equipments(type_id);
CREATE INDEX IF NOT EXISTS idx_equipments_name_cn  ON equipments(name_cn);
CREATE INDEX IF NOT EXISTS idx_equipments_name_jp  ON equipments(name_jp);
CREATE INDEX IF NOT EXISTS idx_equipments_rarity   ON equipments(rarity);

-- ----------------------------------------------------------------------------
-- equipments_fts: 装备全文检索（FTS5，contentless）
-- ----------------------------------------------------------------------------
-- 与 ships_fts 同模式：contentless + 应用层手动维护 + 拼音列。
CREATE VIRTUAL TABLE IF NOT EXISTS equipments_fts USING fts5(
    equipment_id UNINDEXED,
    name_jp,
    name_cn,
    name_en,
    pinyin,
    aliases,
    tokenize='unicode61'
);

-- ----------------------------------------------------------------------------
-- equipment_improvements: 装备改修数据缓存（schema v4 引入，P7.1）
-- ----------------------------------------------------------------------------
-- 来自 kcwikizh/kcwiki-improvement-data 仓库的 improve_data.json（257 KB，344 条）。
-- ImprovementEnhancer 一次拉取全量后，按 equip_id 切片写入此表。
-- status: ok=有数据 / not_found=improve_data.json 中无此 equip_id / failed=网络失败
--（failed 不入此表，由 enhancer 内存重试；not_found 入表避免反复请求）
CREATE TABLE IF NOT EXISTS equipment_improvements (
    equip_id    INTEGER PRIMARY KEY,
    data_json   TEXT,                     -- ImprovementData 序列化（status=ok 时非空）
    fetched_at  INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ok',
    expires_at  INTEGER NOT NULL          -- fetched_at + TTL，过期重新拉取
);

CREATE INDEX IF NOT EXISTS idx_improvements_expires ON equipment_improvements(expires_at);
