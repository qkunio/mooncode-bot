---
name: mooncode-release
description: MoonCode App Store 发版提审步骤（更新日志 → PUBLISH.md → release-tag.sh → GitHub Actions 直连 App Store Connect 构建并提审）。当被要求“拉起 MoonCode vX.Y 审核”时使用。
---

# MoonCode 发版提审

MoonCode 的构建、上传、提审全部由仓库里的 `.github/workflows/release.yml` 完成（`macos-26` runner →
`xcodebuild archive/export` → 直传 App Store Connect → `scripts/asc_submit.py submit` 提交审核，`AFTER_APPROVAL` 自动上架）。
推送 `v*` 带注释 Tag 即触发；Tag 注释就是 zh-Hans 更新日志。你只负责“拉起”。

## 步骤

1. **前置检查**
   ```bash
   git status --porcelain            # 必须为空
   git fetch origin --tags
   git rev-parse -q --verify refs/tags/vX.Y && echo "Tag 已存在，停止"
   ```
2. **写更新日志** 到 `/tmp/mooncode-notes-vX.Y.txt`（简体中文，1~3 条，≤200 字，面向用户）。
3. **更新 `PUBLISH.md`**（仓库 AGENTS.md 要求每次发版同步）：
   - §1「更新日期」改为今天；
   - §6 发布记录表追加一行：`| \`X.Y\` | \`X.Y\` / CI 自动 | \`vX.Y\` | YYYY-MM-DD | GitHub Actions（mooncode-bot 自动） | 已提审（等待审核） | 适配上游 Kimi Code 协议变更 |`
   - 提交：`git commit -am "docs: record vX.Y release in PUBLISH.md"`
4. **打 Tag 并推送**（脚本会更新 `MARKETING_VERSION`、提交、打带注释 Tag 并推送；它会问一次确认，用管道回答 y）：
   ```bash
   printf 'y\n' | ./scripts/release-tag.sh X.Y -f /tmp/mooncode-notes-vX.Y.txt
   ```
5. **确认流水线已启动**：
   ```bash
   gh run list --workflow=release.yml --limit 3
   ```
6. 写 handoff JSON。

## 失败处理
- 脚本报 Tag 已存在 / 工作区不干净：停止并在 handoff 的 `error` 字段说明，不要 `--force`、不要删 Tag。
- `git push` 被拒：说明 main 有新提交，`git pull --rebase` 一次后重试第 4 步；仍失败则停止。
