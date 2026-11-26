---
name: kap-compat
description: MoonCode 适配 Kimi Code KAP 协议变更的通用套路与检查点。实现或审查协议兼容补丁时使用。
---

# KAP 协议兼容套路

## 代码地图（MoonCode）
| 关注点 | 文件 |
|---|---|
| REST 调用、路由回退 | `KimiCode/Core/KapClient.swift`、`KimiCode/Core/KapServicing.swift` |
| 响应信封 `{code,msg,data}`、错误码 | `KimiCode/Core/Envelope.swift` |
| 数据模型 / 解码 | `KimiCode/Core/Models.swift`、`KimiCode/Core/JSONValue.swift` |
| WebSocket 订阅与帧 | `KimiCode/Core/EventStream.swift`、`KimiCode/Core/RuntimeFrames.swift` |
| 对话 reducer（turns / items） | `KimiCode/Core/Conversation.swift`、`KimiCode/Core/Transcript.swift` |
| 会话页状态 | `KimiCode/Features/Session/ChatModel.swift`、`ConversationBuilder.swift` |
| 体验模式假后端（协议变了也要能编译） | `KimiCode/Core/DemoClient.swift` |
| 离线检查脚本 | `scripts/check-attachments.sh`、`scripts/check-device-usage.sh` |

## 常用模式
1. **路由改名/删除**：新路由优先；仅在 HTTP 404 或信封 40401/40001（路由不存在）时回退旧路由，并记住降级结果避免每次试错。
2. **响应结构变化**：自定义 `init(from:)` 同时接受新旧两种形状（参考 `SessionSummary`、`SessionProfileResponse` 的写法）。
3. **字段改为可选 / 新增枚举值**：改为可选或 `RawRepresentable` + `unknown` 兜底，不要让整条消息解码失败。
4. **WS 帧改名**：`EventStream` 的 `switch` 同时接收新旧帧名（参考 `context` / `usage`）。
5. **新能力探测**：优先读 `GET /api/v1/meta` 的 `data.capabilities`，不要靠版本号字符串比较。
6. **请求体**：服务端对象多为 `closed`，不要发送已删除字段；新增必填字段必须带上。

## 完成前自检
- [ ] `Grep` 同一路由/帧名的所有调用点都已处理（含 `DemoClient`）。
- [ ] 旧服务端路径仍可用。
- [ ] `reference/README.md` 追加了本次兼容小节。
- [ ] 验证命令全部通过。
