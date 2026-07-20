"""SourceAdapter 抽象基类。

子类需要实现：
- fetch: 从上游拉取原始数据（含版本指纹）
- normalize_ships: 将原始数据规整为统一 ship schema 的 dict 流
- normalize_slotitems: 装备 dict 流（P7 起要求；旧实现默认空迭代器）
- normalize_equiptypes: 装备类型 dict 流（P7 起要求；旧实现默认空迭代器）

子类可以覆盖：
- priority: 该源在某字段上的优先级（数字越大优先级越高）
- min_data_version / max_data_version: 适配器支持的上游版本范围，
  不在范围内的源数据将被跳过（用于版本漂移保护）
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class RawData:
    """数据源原始数据快照。

    payload 是上游 JSON 反序列化后的对象，结构由各 SourceAdapter 自行约定。
    version 是版本指纹（commit_sha / tag），用于：
    - 与 sources 表中上次抓取版本对比，决定是否需要重算融合
    - 写入 Ship.provenance，便于追溯
    """

    source: str
    version: str  # commit_sha 或 tag
    fetched_at: int  # unix 秒
    payload: Any


class SourceAdapter(ABC):
    """数据源适配器抽象基类。"""

    name: str = ""
    # 适配器支持的上游数据版本范围（语义版本字符串）
    # 上游 bump 主版本号时，应同步更新这里；不匹配时 fusion 跳过该源
    min_data_version: str = "0.1.0"
    max_data_version: str = "999.999.999"

    @abstractmethod
    async def fetch(self, client: httpx.AsyncClient) -> RawData:
        """从上游拉取原始数据。

        实现要点（P2 阶段会落实）：
        - 通过 GitHub API 获取最新 commit_sha（用于版本指纹）
        - 通过 raw.githubusercontent.com 或 jsDelivr CDN 拉取 JSON
        - 网络异常应抛出，由上层 pipeline 决定是否回退到旧数据
        """

    @abstractmethod
    def normalize_ships(self, raw: RawData) -> Iterator[dict[str, Any]]:
        """将原始数据规整为 ship schema 的 dict 流。

        规整后的 dict 应能直接 Ship(**dict) 接受（或部分字段为 None）。
        不在此做字段优先级裁决 —— 那是 fusion.py 的事。
        """

    def normalize_slotitems(self, raw: RawData) -> Iterator[dict[str, Any]]:
        """将原始数据规整为 equipment schema 的 dict 流（P7）。

        默认返回空迭代器；提供装备数据的源应覆盖此方法。
        规整后的 dict 应能直接 Equipment(**dict) 接受（或部分字段为 None）。
        """
        return iter(())

    def normalize_equiptypes(self, raw: RawData) -> Iterator[dict[str, Any]]:
        """将原始数据规整为 equipment_type schema 的 dict 流（P7）。

        默认返回空迭代器；提供装备类型字典的源应覆盖此方法。
        输出 dict 含 type_id / name_jp / name_cn / name_en 字段。
        """
        return iter(())

    def priority(self, field: str) -> int:
        """该源在指定字段上的优先级（数字越大优先级越高）。

        默认所有字段同等优先级；子类可覆盖以表达业务知识，
        例如「kcwiki 的中文名比 kc3kai 准确」。
        """
        return 1
