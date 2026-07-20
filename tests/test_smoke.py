"""P1 骨架冒烟测试。

不依赖 nonebot 运行时（直接 import 包会被 get_plugin_config 阻塞），
改为 AST 语法校验 + 直接文件读取。后续阶段补完整单测
（mock httpx、内存 sqlite 等）。
"""
from __future__ import annotations

import ast
from pathlib import Path


PKG_ROOT = Path(__file__).resolve().parent.parent / "src" / "nonebot_plugin_kancolle"
REPO_ROOT = PKG_ROOT.parent.parent


def _parse(rel: str) -> ast.AST:
    """读取包内文件并以 AST 解析；语法错误会抛 SyntaxError。"""
    src = (PKG_ROOT / rel).read_text(encoding="utf-8")
    return ast.parse(src)


def test_init_parses() -> None:
    """__init__.py 语法正确，且定义了 __plugin_meta__。"""
    tree = _parse("__init__.py")
    assigns = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "__plugin_meta__" for t in n.targets)
    ]
    assert assigns, "__plugin_meta__ 未定义"


def test_config_parses() -> None:
    _parse("config.py")


def test_models_parses() -> None:
    _parse("data/models.py")


def test_source_adapter_parses() -> None:
    _parse("data/sources/base.py")


def test_schema_sql_has_core_tables() -> None:
    """schema.sql 包含第一阶段所需的全部核心表（P7.1 起含装备+改修表）。"""
    schema = (PKG_ROOT / "schema.sql").read_text(encoding="utf-8")
    for table in (
        "meta", "ships", "ships_fts", "aliases", "sources", "conflicts",
        "equipments", "equipments_fts", "equipment_types",
        "equipment_improvements",
    ):
        assert table in schema, f"缺少表 {table}"


def test_schema_version_recorded() -> None:
    """schema.sql 默认写入 schema_version=4（P7.1 起升级）。"""
    schema = (PKG_ROOT / "schema.sql").read_text(encoding="utf-8")
    assert "schema_version" in schema and "'4'" in schema


def test_source_adapter_contract() -> None:
    """SourceAdapter 是抽象基类，声明了 fetch / normalize_ships / priority（P7 起含装备方法）。"""
    tree = _parse("data/sources/base.py")
    class_defs = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "SourceAdapter"
    ]
    assert class_defs
    method_names = {
        n.name for n in ast.walk(class_defs[0])
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {
        "fetch", "normalize_ships", "normalize_slotitems", "normalize_equiptypes", "priority"
    } <= method_names


def test_pyproject_declares_dependencies() -> None:
    """pyproject.toml 声明了第一阶段所需关键依赖。"""
    pp = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for dep in (
        "nonebot2",
        "nonebot-adapter-onebot",
        "nonebot-plugin-localstore",
        "nonebot-plugin-apscheduler",
        "nonebot-plugin-alconna",
        "nonebot-plugin-htmlrender",
        "httpx",
        "jinja2",
        "pypinyin",
        "rapidfuzz",
        "aiofiles",
    ):
        assert dep in pp, f"缺少依赖 {dep}"
