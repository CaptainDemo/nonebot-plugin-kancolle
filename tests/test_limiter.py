"""utils/limiter.py 的 maybe_apply_prefix_variance 单元测试。

覆盖矩阵：
- 配置开关关闭 → 原样返回
- 配置开关开启 + 插件缺失（_variance=None） → 原样返回
- 配置开关开启 + 插件可用 → 调用 prefix_variance.apply
- prefix_variance.apply 抛异常 → 原样返回（绝不阻塞发送）
- 空字符串 → 原样返回
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from nonebot_plugin_kancolle.utils import limiter as limiter_mod
from nonebot_plugin_kancolle.utils.limiter import maybe_apply_prefix_variance


@dataclass
class _FakeConfig:
    kancolle_use_prefix_variance: bool


@dataclass
class _FakeVariance:
    """prefix_variance 的替身；记录最近一次调用便于断言。"""

    return_value: str = "PREFIXED|original"
    calls: int = 0
    raise_exc: Exception | None = None

    def apply(self, message: str, *, separator: str = "\n") -> str:
        self.calls += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        # 把传入文本拼到固定前缀后，方便断言「确实调到了这里」
        return f"PREFIXED|{message}"


def _patch_config(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    """把 get_config 替换成返回受控配置。"""
    cfg = _FakeConfig(kancolle_use_prefix_variance=enabled)
    monkeypatch.setattr(limiter_mod, "get_config", lambda: cfg)


def test_disabled_returns_original(monkeypatch: pytest.MonkeyPatch) -> None:
    """开关关闭：即便 _variance 可用，也不应被调用。"""
    fake = _FakeVariance()
    monkeypatch.setattr(limiter_mod, "_variance", fake)
    _patch_config(monkeypatch, enabled=False)

    assert maybe_apply_prefix_variance("hi") == "hi"
    assert fake.calls == 0


def test_enabled_but_plugin_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """开关开启、但 message-limiter 未安装：原样返回。"""
    monkeypatch.setattr(limiter_mod, "_variance", None)
    _patch_config(monkeypatch, enabled=True)

    assert maybe_apply_prefix_variance("hi") == "hi"


def test_enabled_and_plugin_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """开关开启、插件可用：调用 apply 并返回其结果。"""
    fake = _FakeVariance()
    monkeypatch.setattr(limiter_mod, "_variance", fake)
    _patch_config(monkeypatch, enabled=True)

    out = maybe_apply_prefix_variance("hello")
    assert fake.calls == 1
    assert out == "PREFIXED|hello"


def test_apply_exception_falls_back_to_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """apply 抛任何异常都必须吞掉、原样返回，绝不阻塞消息发送。"""
    fake = _FakeVariance(raise_exc=RuntimeError("boom"))
    monkeypatch.setattr(limiter_mod, "_variance", fake)
    _patch_config(monkeypatch, enabled=True)

    assert maybe_apply_prefix_variance("hello") == "hello"
    assert fake.calls == 1  # 调用过了，但被 catch


def test_empty_string_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """空串直接返回，连配置都不读，避免无谓扰动与不必要的运行时依赖。"""

    def _raises() -> None:
        raise RuntimeError("get_config should not be called for empty input")

    fake = _FakeVariance()
    monkeypatch.setattr(limiter_mod, "_variance", fake)
    monkeypatch.setattr(limiter_mod, "get_config", _raises)

    assert maybe_apply_prefix_variance("") == ""
    assert fake.calls == 0
