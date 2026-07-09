"""渲染层：jinja2 模板 + htmlrender 截图 + 图片缓存。

设计要点（P5 完整实现）：
- 每个「逻辑面板」单独一张图（基础卡 / 数值面板 / 改造链）
- 缓存 key = f"{entity_type}_{id}_{mode}_{theme}_{data_version}"
- 全程 bytes 流转，发送时用 UniMessage.image(raw=bytes)，
  不依赖本地文件路径（兼容 nonebot / napcat 分离部署）
"""
