# MoonCode × Kimi Code 兼容 Ledger

> 长期记忆（L1）。每次 triage / patch / review 都会读到它，因此必须**短、准、可复用**。
> 由 distiller 阶段自动追加经验；超过 `context.ledger_max_chars` 时自动压缩重写。人工可随时编辑。

## 链路与不变量
- iOS App ⇄ `code-rc.kimi.com`（闭源中转）⇄ 用户电脑上的 `kimi rc` ⇄ 本地 kap-server（`/api/v1`、`/api/v1/ws`）。
- 用户电脑上的 Kimi Code 版本不一：**新协议优先，旧协议回退**；只有“路由不存在”（HTTP 404 / 信封 40401、40001）才降级。
- 上游承诺核心会话接口向后兼容（`/meta`、`/sessions*`、`/turns`、`/prompts`、`/interactions`、`/files`、WS `subscribe/turns/turn.*/item.*/interaction/agent/task.*`）；
  第一方宿主管理接口（`/config`、`/providers`、`/models`、`/oauth/*`、`/fs:*`、`/workspaces*`…）**随产品演进、不保证兼容**——MoonCode 的供应商设置页最容易被打破。
- 新能力探测看 `GET /api/v1/meta` 的 `data.capabilities` 与 `kimi_agent_protocol_version`。
- 契约真相源：上游 `packages/kap-server/schema/contract.txt`（一行一个字段）；`docs/kap.md` 是简明指南。

## 兼容史（浓缩）
- **2.4**：删旧 approvals/questions 列表与回写，改平铺 `interaction` 帧 + `POST /sessions/{id}/interactions/{interaction_id}`；订阅确认后重发 pending 全量。
- **2.5**：只保留 `subscribe_v2`；状态改扁平 `agent` / `usage` 帧（全量替换语义）；`GET /sessions/{id}/status` → 404。
- **2.6**：`/transcript` 删除 → `GET /sessions/{id}/agents/{agent_id}/turns`（`{turns,has_more}`，`before_ordinal` 分页）；
  WS 去掉 hello，直接 `subscribe` → `response` 确认；服务端 10s `ping` 需回 `pong`；增量帧 `turn.*`、`item.*`、`turns.removed`；
  `item.delta` 直接带追加文本；`item.output` 需按任意 JSON 解码；撤销改 `{turn_id, agent_id}`。
- **2.7 / KAP 1.0.0**：`Session` 改 `activity: idle|turn|background` + `last_turn_outcome`，`cwd` 顶层；`/profile` 返回 `{session, agent}`；
  停止 `:abort` → `:cancel`；WS `usage` → `context`；`agent.status` 为 `idle|running|ended` + 独立 `compacting`；新增 `GET /sessions/{id}/interactions`；供应商协议新增 `bedrock`。

## 适配模式
- 结构变化：自定义 `init(from:)` 同时接受新旧形状（`SessionSummary`、`SessionProfileResponse` 先例）。
- 帧改名：`EventStream` 同时接收新旧名（`context` / `usage` 先例）。
- 枚举扩展：可选 + `unknown` 兜底，避免整帧解码失败。
- 每次适配在 MoonCode `reference/README.md` 追加一节；`DemoClient` 也要跟着改，否则体验模式编译失败。
