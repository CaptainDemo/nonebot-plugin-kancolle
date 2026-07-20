"""ImprovementEnhancer 解析器单测（P7.1）。

不依赖网络：直接构造 improve_data.json 子集，验证解析器对各种边界的处理。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nonebot_plugin_kancolle.data.improvement_enhancer import (
    _parse_improvement_entry,
)
from nonebot_plugin_kancolle.data.models import ImprovementData

FIXTURE = Path(__file__).parent / "fixtures" / "improve_data_sample.json"


@pytest.fixture()
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------
# 边界处理
# ----------------------------------------------------------------------

def test_parse_normal_entry_with_upgrade(payload: dict) -> None:
    """087: 3 段 material + 升级链 + 1 recipe。"""
    data = _parse_improvement_entry(87, payload["087"])
    assert data is not None
    assert isinstance(data, ImprovementData)
    assert data.equip_id == 87
    assert len(data.entries) == 1
    entry = data.entries[0]
    # 升级链
    assert entry.upgrade is not None
    assert entry.upgrade.target_id == 228
    assert entry.upgrade.target_name == "20.3cm(3号)連装砲"
    # 3 段 material
    assert len(entry.materials) == 3
    assert entry.materials[0].development == [1, 2]
    assert entry.materials[0].improvement_res == [1, 1]
    assert entry.materials[0].item_name == "7.7mm機銃"
    assert entry.materials[0].item_count == 1
    # 基础消耗
    assert entry.fuel == 70
    assert entry.ammo == 70
    # 1 recipe
    assert len(entry.recipes) == 1
    recipe = entry.recipes[0]
    assert len(recipe.day) == 7
    assert all(recipe.day)  # 全 True
    assert recipe.secretary_names == ["鳥海改二", "摩耶改二", "愛宕", "高雄"]


def test_parse_two_stage_material(payload: dict) -> None:
    """025: 2 段 material（无升级链），day 部分日期可用。"""
    data = _parse_improvement_entry(25, payload["025"])
    assert data is not None
    entry = data.entries[0]
    assert entry.upgrade is None  # upgrade.id=0 视为无升级
    assert len(entry.materials) == 2
    recipe = entry.recipes[0]
    assert recipe.day == [False, True, False, True, False, True, False]


def test_parse_abnormal_day_length(payload: dict) -> None:
    """285: day 数组长度 35（已知数据 bug），防御性截断到 7。"""
    data = _parse_improvement_entry(285, payload["285"])
    assert data is not None
    recipe = data.entries[0].recipes[0]
    assert len(recipe.day) == 7
    # 截取前 7 个：[T,F,T,F,T,F,T]
    assert recipe.day == [True, False, True, False, True, False, True]


def test_parse_empty_req(payload: dict) -> None:
    """999: req=[] 但有 material，应该正常解析。"""
    data = _parse_improvement_entry(999, payload["999"])
    assert data is not None
    entry = data.entries[0]
    assert entry.recipes == []
    assert len(entry.materials) == 1


def test_parse_multiple_recipes(payload: dict) -> None:
    """1000: 多 recipe 组合，验证全部解析。"""
    data = _parse_improvement_entry(1000, payload["1000"])
    assert data is not None
    entry = data.entries[0]
    assert len(entry.recipes) == 2
    r0, r1 = entry.recipes
    assert r0.secretary_names == ["赤城", "加賀"]
    assert r0.day == [True, True, False, False, False, False, False]
    assert r1.secretary_names == ["蒼龍", "飛龍"]
    assert r1.day == [False, False, True, True, False, False, False]


# ----------------------------------------------------------------------
# 异常输入
# ----------------------------------------------------------------------

def test_parse_non_dict_returns_none() -> None:
    assert _parse_improvement_entry(1, "not a dict") is None
    assert _parse_improvement_entry(1, None) is None
    assert _parse_improvement_entry(1, []) is None


def test_parse_no_improvement_field_returns_none() -> None:
    assert _parse_improvement_entry(1, {"id": 1, "name": "x"}) is None
    assert _parse_improvement_entry(1, {"improvement": []}) is None


def test_normalize_day_static_method() -> None:
    """ImprovementRecipe.normalize_day 工具方法。"""
    from nonebot_plugin_kancolle.data.models import ImprovementRecipe

    assert ImprovementRecipe.normalize_day(None) == [False] * 7
    assert ImprovementRecipe.normalize_day([]) == [False] * 7
    assert ImprovementRecipe.normalize_day([True, False]) == [True, False, False, False, False, False, False]
    assert ImprovementRecipe.normalize_day([True] * 35) == [True] * 7
    assert ImprovementRecipe.normalize_day([1, 0, 1, 0, 1, 0, 1]) == [
        True, False, True, False, True, False, True
    ]


# ----------------------------------------------------------------------
# 模型序列化往返
# ----------------------------------------------------------------------

def test_improvement_data_round_trip(payload: dict) -> None:
    """ImprovementData JSON 序列化往返保持字段值。"""
    data = _parse_improvement_entry(87, payload["087"])
    assert data is not None
    j = data.model_dump_json()
    restored = ImprovementData.model_validate_json(j)
    assert restored.equip_id == 87
    assert len(restored.entries) == 1
    assert restored.entries[0].upgrade is not None
    assert restored.entries[0].upgrade.target_id == 228
