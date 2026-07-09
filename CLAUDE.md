# Claude Code 执行原则

## 项目简介

`nonebot-plugin-kancolle` 是一个 NoneBot2 插件，用于查询舰队Collection（舰これ / 舰娘）相关数据：舰娘、装备、任务、海域攻略等。输出可为纯文本，或将复杂信息渲染为图片后发出。

## Python 环境选择规则（重要）

该仓库同时在两台机器上编辑，工具链不同。**执行任何 Python / 包管理 / 测试命令前，必须先按当前工作目录判定所在机器，再选择对应工具链**：

- **远程共享目录** —— 路径前缀为 `Z:\`、`Z:/`，或 UNC 形如 `\\SPIRENAS\Share\...`。该机器使用 **uv** 管理 Python，虚拟环境位于 `.uv-venv`。
  - 安装依赖：`uv sync`
  - 运行命令：`uv run pytest` / `uv run python -m pip install -e .`
  - **禁止**在该路径下使用 `pyenv` 或创建 `.venv`，除非用户明确要求。

- **本机目录** —— 路径不在上述前缀下（如 `C:\`、`D:\` 等本机盘符）。本机使用 **pyenv** 管理 Python，虚拟环境位于 `.venv`。
  - 解释器选择：`pyenv versions` / `pyenv which python`
  - 安装依赖：`python -m pip install -e .`
  - 运行测试：`.\.venv\Scripts\python.exe -m pytest`
  - **禁止**在该路径下使用 `uv`、创建 `.uv-venv` 或修改 `UV_*` 环境变量，除非用户明确要求。

- 若命令在预期环境下失败，先排查该环境的解释器、PATH 与虚拟环境状态，**不要**直接切换到另一种工具或新建另一个虚拟环境作为变通。

## 代码风格

- 4 空格缩进，行长 ≤ 100。
- 包/模块名 `snake_case`，类名 `PascalCase`。
- 类型注解齐全（项目启用 mypy strict）。
- 注释使用中文。

## 参考资料

- [NoneBot2 文档](https://v2.nonebot.dev/)
- [Pydantic 文档](https://docs.pydantic.dev/)
- [Python 类型注解](https://docs.python.org/zh-cn/3/library/typing.html)
- [PEP 8](https://pep.python.org/pep-0008/)
