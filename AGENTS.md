# AGENTS.md

面向在本仓库工作的 AI Agent。

- 这是 **mooncode-bot**：追踪 `MoonshotAI/kimi-code` main 的 KAP 契约变化，用 `kimi` 为 `qkunio/mooncode` 自动生成兼容补丁、
  验证、合并并拉起 App Store 提审。先读 [README.md](README.md) 与 [docs/CONTEXT.md](docs/CONTEXT.md)。
- 编排器是纯 Python 标准库，需兼容 Python 3.9（本机 `/usr/bin/python3`）：不要用 `match`、`X | Y` 运行时类型、`tomllib`。
- 改动后运行：`python3 -m unittest discover -s tests`。
- **上下文纪律**是本项目的核心约束：新增阶段时必须给出 ContextPack 预算、handoff 字段约定，并保证阶段间只传 handoff。
- `memory/` 与 `state/` 由 bot 自动维护并 commit；人工修改 `memory/ledger.md` 是允许的，但保持精炼。
- 不要在仓库中写入任何 Token / API Key / App Store Connect 凭证。
- 没有用户明确指令时，不要以 `BOT_ARMED=true` 运行 `tick` 或 `release`。
