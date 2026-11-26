"""上下文管理层 —— 本项目的核心。

设计要点（详见 docs/CONTEXT.md）：
- L0 宪法：agents/*.md 系统提示词，固定、短小；
- L1 记忆：memory/ledger.md + 最近几条 episodes，有硬上限，由 distiller 压缩；
- L2 任务包：每个阶段由编排器按预算拼装的 ContextPack（只放“这一步需要的”）；
- L3 工作上下文：kimi 会话内部，阶段结束即丢弃，只有结构化 handoff 往下游传。
"""
