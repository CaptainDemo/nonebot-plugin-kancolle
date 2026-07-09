# Seed 数据包占位

此目录用于存放插件首次安装时的兜底数据 `master.db.gz`。

生成方式（仓库根目录）：

```bash
uv run --no-project \
    --with nonebot2 --with httpx --with pydantic \
    python scripts/build_seed.py
```

生成后此 README 应被替换为实际 `master.db.gz` 文件。
