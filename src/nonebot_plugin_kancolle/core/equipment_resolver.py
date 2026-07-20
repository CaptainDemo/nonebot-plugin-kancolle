"""EquipmentResolver：用户输入 -> Equipment 的四层匹配。

匹配顺序（前者命中即返回）：
1. 精确名：jp/cn/en 任一完全相等（大小写不敏感）
2. 拼音：用户输「lingshishangzhen」匹配中文名拼音
3. FTS5 全文检索：分词匹配
4. rapidfuzz 兜底：相似度 >= min_fuzzy_score 的前 N 个

与 ShipResolver 的区别：**跳过"改造后缀剥离"层**——装备多数无后缀概念，
且装备命名规律与舰娘不同（如「20.3cm連装砲」「零式水上偵察機」）。

性能：
- 启动时构建拼音索引（<50ms）
- 单次查询 <50ms
"""
from __future__ import annotations

from ..data.models import Equipment
from ..data.store import Store
from ..utils.logger import log
from ..utils.pinyin import to_pinyin
from .result import EquipmentResolveResult


class EquipmentResolver:
    """装备解析器。无状态外部依赖，仅持有 Store 与缓存索引。"""

    def __init__(
        self,
        store: Store,
        max_list_items: int = 5,
        min_fuzzy_score: int = 60,
    ) -> None:
        self._store = store
        self._max_list = max_list_items
        self._min_fuzzy = min_fuzzy_score
        # 拼音索引懒构建；首次 resolve 时构建一次，之后常驻内存
        self._pinyin_index: dict[str, set[int]] | None = None

    # ------------------------------------------------------------------
    # 索引
    # ------------------------------------------------------------------
    def _ensure_index(self) -> None:
        if self._pinyin_index is not None:
            return
        pinyin_idx: dict[str, set[int]] = {}
        for equip in self._store.all_equipments():
            if equip.name.cn:
                py = to_pinyin(equip.name.cn)
                if py:
                    pinyin_idx.setdefault(py, set()).add(equip.id)
        self._pinyin_index = pinyin_idx
        log.info(f"equipment resolver pinyin index built: {len(pinyin_idx)} keys")

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def resolve(self, query: str) -> EquipmentResolveResult:
        """解析用户输入。线程安全（索引构建后只读）。"""
        self._ensure_index()
        q = query.strip()
        if not q:
            return EquipmentResolveResult.none("查询内容为空")

        # Stage 1: 精确名
        exact = self._store.find_equipment_by_exact_name(q)
        if exact:
            return EquipmentResolveResult.single(exact)

        # Stage 2: 拼音
        result = self._resolve_via_pinyin(q)
        if result is not None:
            return result

        # Stage 3: FTS5
        result = self._resolve_via_fts(q)
        if result is not None:
            return result

        # Stage 4: rapidfuzz 兜底
        result = self._resolve_via_fuzzy(q)
        if result is not None:
            return result

        return EquipmentResolveResult.none(f"未找到与「{q}」匹配的装备")

    # ------------------------------------------------------------------
    # Stage 实现
    # ------------------------------------------------------------------
    def _resolve_via_pinyin(self, q: str) -> EquipmentResolveResult | None:
        """用户输入作为拼音，匹配 cn 名拼音。"""
        assert self._pinyin_index is not None
        q_normalized = q.lower().replace(" ", "")
        if not q_normalized:
            return None
        equip_ids = self._pinyin_index.get(q_normalized)
        if not equip_ids:
            return None
        equips = list(self._store.get_equipments_by_ids(list(equip_ids)).values())
        if not equips:
            return None
        if len(equips) == 1:
            return EquipmentResolveResult.single(equips[0])
        return EquipmentResolveResult.multiple(
            equips[: self._max_list],
            hint="pinyin",
            message=f"拼音「{q}」匹配 {len(equips)} 件装备",
        )

    def _resolve_via_fts(self, q: str) -> EquipmentResolveResult | None:
        """FTS5 全文检索。"""
        hits = self._store.search_equipment_fts(q, limit=self._max_list)
        if not hits:
            return None
        equips = list(self._store.get_equipments_by_ids([h[0] for h in hits]).values())
        if not equips:
            return None
        if len(equips) == 1:
            return EquipmentResolveResult.single(equips[0])
        return EquipmentResolveResult.multiple(
            equips, hint="fts", message=f"全文匹配 {len(equips)} 件装备"
        )

    def _resolve_via_fuzzy(self, q: str) -> EquipmentResolveResult | None:
        """rapidfuzz 兜底：对全部装备的所有名字计算相似度。"""
        try:
            from rapidfuzz import fuzz
        except ImportError:
            log.warning("rapidfuzz not installed; fuzzy matching disabled")
            return None

        q_lower = q.lower()
        scored: list[tuple[Equipment, int]] = []
        for equip in self._store.all_equipments():
            best = 0
            for name in (equip.name.jp, equip.name.cn, equip.name.en):
                if name:
                    score = int(fuzz.WRatio(q_lower, name.lower()))
                    if score > best:
                        best = score
            if best >= self._min_fuzzy:
                scored.append((equip, best))

        if not scored:
            return None
        scored.sort(key=lambda x: -x[1])

        # 单一候选且分数很高 → single
        if len(scored) == 1 and scored[0][1] >= 90:
            return EquipmentResolveResult.single(scored[0][0])
        return EquipmentResolveResult.multiple(
            [s for s, _ in scored[: self._max_list]],
            hint="fuzzy",
            message=f"模糊匹配 {len(scored)} 件装备，按相似度排序",
        )
