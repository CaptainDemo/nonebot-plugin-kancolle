"""数据更新机制。

子模块：
- pipeline: 完整更新流程（run_fusion + 缓存失效 + 状态摘要）
- seed: 首启时从包内 seed/master.db.gz 解压兜底
- scheduler: apscheduler 定时任务 + 启动触发

为避免在测试环境（nonebot 未初始化）触发副作用，本 __init__.py 仅做声明，
scheduler 注册由 commands.admin 模块在 matcher 注册时按需触发，或由 main
__init__.py 显式调用 setup_scheduler_hooks。
"""
