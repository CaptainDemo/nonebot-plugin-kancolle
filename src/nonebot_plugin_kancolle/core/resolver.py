"""ShipResolver：用户输入 -> Ship 的五层匹配。

匹配顺序（前者命中即返回）：
1. 精确名：jp/cn/en/romaji 任一完全相等（大小写不敏感）
2. 改造后缀剥离 + 核心名索引：用户输「大和改二」→ 剥离「改二」→ 核心名「大和」
   - 若核心名仅对应一艘船 → single
   - 若对应多艘但用户输入的后缀能锁定具体一艘 → single
   - 否则 → multiple，让用户从改造链中选
3. 拼音：用户输「dahe」匹配中文名拼音「dahe」（大和）
4. FTS5 全文检索：分词匹配
5. rapidfuzz 兜底：相似度 >= min_fuzzy_score 的前 N 个

性能：
- 启动时构建核心名索引 + 拼音索引（1681 船，<10ms）
- 单次查询 <50ms（rapidfuzz Cython 优化，1681×4 名字全扫只需 ~5ms）
"""
from __future__ import annotations

from ..data.models import RemodelSuffix, Ship
from ..data.store import Store
from ..utils.logger import log
from ..utils.pinyin import to_pinyin
from .result import ResolveResult


class ShipResolver:
    """舰娘解析器。无状态外部依赖，仅持有 Store 与缓存索引。"""

    def __init__(
        self,
        store: Store,
        max_list_items: int = 5,
        min_fuzzy_score: int = 60,
    ) -> None:
        self._store = store
        self._max_list = max_list_items
        self._min_fuzzy = min_fuzzy_score
        # 索引懒构建；首次 resolve 时构建一次，之后常驻内存
        self._core_index: dict[str, set[int]] | None = None
        self._pinyin_index: dict[str, set[int]] | None = None

    # ------------------------------------------------------------------
    # 索引
    # ------------------------------------------------------------------
    def _ensure_index(self) -> None:
        if self._core_index is not None and self._pinyin_index is not None:
            return
        core_idx: dict[str, set[int]] = {}
        pinyin_idx: dict[str, set[int]] = {}
        for ship in self._store.all_ships():
            for core in _extract_core_names(ship):
                core_idx.setdefault(core, set()).add(ship.id)
            if ship.name.cn:
                py = to_pinyin(ship.name.cn)
                if py:
                    pinyin_idx.setdefault(py, set()).add(ship.id)
        self._core_index = core_idx
        self._pinyin_index = pinyin_idx
        log.info(
            f"resolver index built: {len(core_idx)} core names, "
            f"{len(pinyin_idx)} pinyin keys"
        )

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def resolve(self, query: str) -> ResolveResult:
        """解析用户输入。线程安全（索引构建后只读）。"""
        self._ensure_index()
        q = query.strip()
        if not q:
            return ResolveResult.none("查询内容为空")

        # Stage 1: 精确名
        exact = self._store.find_by_exact_name(q)
        if exact:
            return ResolveResult.single(exact)

        # Stage 2: 改造后缀剥离 + 核心名索引
        result = self._resolve_via_remodel_suffix(q)
        if result is not None:
            return result

        # Stage 3: 拼音
        result = self._resolve_via_pinyin(q)
        if result is not None:
            return result

        # Stage 4: FTS5
        result = self._resolve_via_fts(q)
        if result is not None:
            return result

        # Stage 5: rapidfuzz 兜底
        result = self._resolve_via_fuzzy(q)
        if result is not None:
            return result

        return ResolveResult.none(f"未找到与「{q}」匹配的舰娘")

    # ------------------------------------------------------------------
    # Stage 实现
    # ------------------------------------------------------------------
    def _resolve_via_remodel_suffix(self, q: str) -> ResolveResult | None:
        """剥离改造后缀，按核心名查索引。"""
        assert self._core_index is not None
        core, suffix = _split_core_and_suffix(q)
        core_lower = core.lower()
        if not core_lower or core_lower not in self._core_index:
            return None

        ship_ids = self._core_index[core_lower]
        ships = list(self._store.get_ships_by_ids(list(ship_ids)).values())
        if not ships:
            return None

        # 用户输入有特定后缀 → 在改造链中找精确改造形态
        if suffix:
            target = _find_specific_remodel(ships, q, suffix)
            if target:
                return ResolveResult.single(target)

        # 链中唯一 → 直接返回
        if len(ships) == 1:
            return ResolveResult.single(ships[0])

        # 多个 → 让用户选
        return ResolveResult.multiple(
            ships[: self._max_list],
            hint="chain",
            message=f"找到 {len(ships)} 艘相关舰娘，请选择",
        )

    def _resolve_via_pinyin(self, q: str) -> ResolveResult | None:
        """用户输入作为拼音，匹配 cn 名拼音。"""
        assert self._pinyin_index is not None
        q_normalized = q.lower().replace(" ", "")
        if not q_normalized:
            return None
        ship_ids = self._pinyin_index.get(q_normalized)
        if not ship_ids:
            return None
        ships = list(self._store.get_ships_by_ids(list(ship_ids)).values())
        if not ships:
            return None
        if len(ships) == 1:
            return ResolveResult.single(ships[0])
        return ResolveResult.multiple(
            ships[: self._max_list],
            hint="pinyin",
            message=f"拼音「{q}」匹配 {len(ships)} 艘",
        )

    def _resolve_via_fts(self, q: str) -> ResolveResult | None:
        """FTS5 全文检索。"""
        hits = self._store.search_fts(q, limit=self._max_list)
        if not hits:
            return None
        ships = list(self._store.get_ships_by_ids([h[0] for h in hits]).values())
        if not ships:
            return None
        if len(ships) == 1:
            return ResolveResult.single(ships[0])
        return ResolveResult.multiple(ships, hint="fts", message=f"全文匹配 {len(ships)} 艘")

    def _resolve_via_fuzzy(self, q: str) -> ResolveResult | None:
        """rapidfuzz 兜底：对全部 ship 的所有名字计算相似度。"""
        try:
            from rapidfuzz import fuzz
        except ImportError:
            log.warning("rapidfuzz not installed; fuzzy matching disabled")
            return None

        q_lower = q.lower()
        scored: list[tuple[Ship, int]] = []
        for ship in self._store.all_ships():
            best = 0
            for name in (ship.name.jp, ship.name.cn, ship.name.en, ship.name.romaji):
                if name:
                    score = int(fuzz.WRatio(q_lower, name.lower()))
                    if score > best:
                        best = score
            if best >= self._min_fuzzy:
                scored.append((ship, best))

        if not scored:
            return None
        scored.sort(key=lambda x: -x[1])

        # 单一候选且分数很高 → single
        if len(scored) == 1 and scored[0][1] >= 90:
            return ResolveResult.single(scored[0][0])
        return ResolveResult.multiple(
            [s for s, _ in scored[: self._max_list]],
            hint="fuzzy",
            message=f"模糊匹配 {len(scored)} 艘，按相似度排序",
        )


# ----------------------------------------------------------------------
# 改造后缀剥离工具
# ----------------------------------------------------------------------

def _split_core_and_suffix(query: str) -> tuple[str, str]:
    """把用户输入拆为 (core, suffix)，suffix 是被剥离的改造后缀原文。

    示例：
    - "大和改二" → ("大和", "改二")
    - "Bismarck drei" → ("Bismarck", "drei")
    - "yamato k2" → ("yamato", "k2")
    - "大和" → ("大和", "")   # 无后缀可剥离
    """
    q = query.strip()
    q_lower = q.lower()
    # 长后缀优先（"改二乙" 必须先于 "改二"）
    for suffix_list in (RemodelSuffix.CN, RemodelSuffix.JP, RemodelSuffix.EN):
        for suffix in suffix_list:
            s_lower = suffix.lower()
            if q_lower.endswith(s_lower):
                core = q[: len(q) - len(suffix)].strip()
                return core, suffix
    return q, ""


def _extract_core_names(ship: Ship) -> set[str]:
    """从 ship 的多语言名剥离改造后缀，得到核心名集合（lowercase）。

    一艘船可能贡献多个核心名（不同语言的写法各自剥离后结果不同）。
    """
    cores: set[str] = set()
    for name in (ship.name.jp, ship.name.cn, ship.name.en):
        if not name:
            continue
        core, _ = _split_core_and_suffix(name)
        if core:
            cores.add(core.lower())
    return cores


def _find_specific_remodel(
    candidates: list[Ship], original_query: str, suffix: str
) -> Ship | None:
    """从改造链候选中找与用户输入精确匹配的具体改造形态。

    匹配策略（按精确度递减）：
    1. 用户输入与某候选名字（任意语言）完全相等（大小写不敏感）
    2. 用户输入后缀与某候选名字后缀相同，且核心名一致
    """
    q_lower = original_query.lower()
    # 策略 1
    for ship in candidates:
        for name in (ship.name.jp, ship.name.cn, ship.name.en):
            if name and name.lower() == q_lower:
                return ship

    # 策略 2：用户输入「大和 K2」，候选中有「Yamato Kai2」
    suffix_lower = suffix.lower()
    query_core, _ = _split_core_and_suffix(original_query)
    query_core_lower = query_core.lower()
    for ship in candidates:
        for name in (ship.name.jp, ship.name.cn, ship.name.en):
            if not name:
                continue
            name_lower = name.lower()
            if not name_lower.endswith(suffix_lower):
                continue
            cand_core, _ = _split_core_and_suffix(name)
            if cand_core.lower() == query_core_lower:
                return ship
    return None
