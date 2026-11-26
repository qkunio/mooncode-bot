# 上下文管理设计

mooncode-bot 的价值不在“调用了 AI”，而在于**每次调用 AI 时，它看到的恰好是完成这一步所需的东西，不多不少**。
过去人工和 AI 沟通兼容问题之所以累，是因为每次都要重新组织上下文；这里把“组织上下文”变成代码。

## 1. 四层上下文

```mermaid
flowchart TB
    L0["L0 宪法：agents/*.md + skills/（固定、短）"]
    L1["L1 长期记忆：memory/ledger.md + 最近 episodes（跨运行、定期压缩）"]
    L2["L2 任务包 ContextPack（每阶段源头筛选装配，默认不截断）"]
    L3["L3 工作上下文：kimi 会话内部（阶段结束即丢弃）"]
    L0 --> L3
    L1 --> L2 --> L3
    L3 -- "只输出 handoff JSON" --> H["下一阶段的 L2"]
    L3 -. "transcript 只落盘审计" .-> R["runs/&lt;id&gt;/"]
```

- **L0 宪法**：角色、硬规则（向后兼容、不碰签名与密钥、不 push）、handoff 格式。通过 `--agent-file` 作为系统提示词注入；
  可复用的操作知识放进 `skills/`（`--skills-dir`），由 kimi 按需加载，而不是每次塞进提示词。
- **L1 长期记忆**：`memory/ledger.md` 是“兼容史 + 适配模式 + 不变量”的浓缩版，替代了过去每次都要向 AI 重讲的那段背景。
  `memory/episodes/` 每次运行一条 ≤300 字摘要，只取最近 3 条。
- **L2 任务包**：编排器为每个阶段拼装，详见下文。
- **L3 工作上下文**：kimi 在自己会话里的推理、读文件、跑命令。**永不**向下游传递。

## 2. ContextPack：源头筛选 + 默认不截断

`bot/context/pack.py`。上下文治理的核心是**源头降噪**（只放与 MoonCode 相关的契约差异、源码指针、精简记忆、蒸馏错误），
而不是事后硬切代码。

- 默认 `budget_tokens` 全为 `null`（**不截断**）：所有经过筛选的高信噪比内容完整交给模型（K3.1 Pro 窗口为 1M tokens）；
- 审查阶段的 diff 默认不对代码文件截断，只把二进制资源与超大生成数据文件（如 >20KB 的 `.json`）改写为行数摘要；
- 命令行长度兜底：若某个阶段的 `prompt.md` 超过 100KB，编排器自动改为传一句短指令让 `kimi` 用 `Read` 读取落盘的 `prompt.md`，
  避免触发操作系统 `ARG_MAX` 限制，同时不丢任何内容；
- 可选预算模式：若在 `config/bot.json → context.budget_tokens` 里把某阶段设为整数，仍会按 `priority`（越小越重要）装箱、
  截断并在有 `source` 时留磁盘指针；
- 可观测性：每个包都会落盘 `runs/<id>/<stage>/context.md` + `manifest.json`（记录各段实际 token 与状态），出问题时可以精确复盘。

各阶段装配内容：

| 阶段 | 默认截断上限 | P0 | P1 | P2 | P3+ |
|---|---|---|---|---|---|
| triage | 不限（`null`） | 任务 + handoff 路径 | 过滤后的契约差异 + 源码指针 | 上游 PR 意图 | ledger、去重 shape 展开、完整 kap.md diff、episodes、已忽略操作 |
| patch | 不限（`null`） | 任务 + 完成标准 | triage 结论、验证命令 | 相关契约差异 + 指针 | ledger、episodes |
| fix | 不限（`null`） | “只修这些” | 蒸馏后的错误摘要（≤6k 字符）或审查意见 | — | 原始日志仅指针 |
| review | 不限（`null`） | 审查任务 | triage 结论 | 完整代码 diff（非代码大文件改摘要） | ledger |
| release | 不限（`null`） | 版本/Tag/PR | 更新日志素材 | — | — |
| distill | 不限（`null`） | 任务 | 本次运行结构化记录 | 完整当前 ledger（永不截断） | — |

## 3. 给指针，不给全文

MoonCode 有约 2 万行 Swift。`bot/surface.py` 扫描源码得到“MoonCode 用到的每个 REST 路由 / WS 消息 → 源码位置”，
存为 `memory/surface.json`。它有两个用途：

- **过滤**：契约 diff 只保留 MoonCode 用到的部分（上游一次改动常常涉及几百行契约，真正相关的往往只有几行）；
- **指针**：上下文包里每个变更都附 `MoonCode 位置：KimiCode/Core/KapClient.swift:291`。agent 按图索骥地读几十行，
  而不是通读整个仓库。大范围探索交给 kimi 的 `explore` subagent，结论回流、过程不回流。

## 4. 阶段隔离与 handoff

每个 AI 阶段 = 一次全新的 `kimi -p` 会话，输入是 L2 包，输出是一个 JSON handoff（字段在 agent 定义里约定，
编排器校验必填字段）。阶段间只传 handoff：

```
detect.json ──► triage.json ──► patch.json ──► review.json ──► release.json
                                   ▲    │
                      错误摘要 (fix) │    ▼
                                 verify（确定性）
```

- **同阶段续接**：patch 的修复轮次用 `kimi -S <session_id>` 续接同一会话——它需要记得自己刚才改了什么；
  但每轮只追加一份蒸馏后的错误摘要（≤6k 字符），而不是整份日志。
- **跨阶段隔离**：reviewer 永远是新会话，看不到 patcher 的思考过程，只看「待适配项 + diff」——这是刻意的，
  避免“自己审自己”。
- **只读护栏**：triage / review 结束后编排器检查 `git status`，被改过即回滚并判失败。

## 5. 日志蒸馏

`bot/context/logs.py`：xcodebuild 动辄几万行。只提取 `file:line:col: error:` 行（去重、转为仓库相对路径、带 2 行源码上下文）
和 `** BUILD FAILED **`、测试断言等，压到 ≤6k 字符。原始日志落盘 `runs/<id>/verify-N.log`，摘要里给出路径。

## 6. 记忆的写入与压缩

每次涉及 AI 的运行结束（成功或失败）后，distiller 阶段（只读 + 只能写 handoff）产出：

- `episode`：≤300 字摘要 → `memory/episodes/<run>.md`；
- `lessons`：0~5 条可复用经验 → 去重后追加到 ledger 的「自动沉淀的经验」；
- ledger 超过 `ledger_max_chars` 时，额外要求输出 `ledger_rewrite`：压缩到上限的 70%，合并同类项、删除过时细节。

记忆随 `state/` 一起由 bot commit 回本仓库，所以它是**可审阅、可回滚、可人工编辑**的。

## 7. 什么不进上下文

- kimi 的完整事件流（`*.transcript.jsonl`）——只用于审计，CI 中作为 artifact 保留 7 天；
- 原始构建日志；
- 上游与 MoonCode 无关的契约变更（只给一个操作名列表，最低优先级）；
- 任何密钥。App Store Connect 凭证只存在于 MoonCode 仓库的 GitHub Environment 中。
