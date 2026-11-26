#!/usr/bin/env bash
# 在本机用 launchd 每 15 分钟跑一次 mooncode-bot（复用本机 Xcode 与已登录的 kimi / gh）。
#
#   ./scripts/install-launchd.sh            # 安装（默认演练模式）
#   ./scripts/install-launchd.sh --armed    # 安装并武装（会真的 push / merge / 打 Tag）
#   ./scripts/install-launchd.sh --uninstall
set -euo pipefail

LABEL="io.qkun.mooncode-bot"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$HOME/Library/Logs/mooncode-bot"

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  rm -f "$PLIST"
  echo "已卸载 ${LABEL}"
  exit 0
fi

ARMED="false"
[ "${1:-}" = "--armed" ] && ARMED="true"
mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>WorkingDirectory</key><string>${ROOT}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string><string>-lc</string>
    <string>python3 -m bot tick &amp;&amp; git add state memory &amp;&amp; (git diff --cached --quiet || (git commit -qm "chore(bot): local tick" &amp;&amp; git push -q))</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>BOT_ARMED</key><string>${ARMED}</string></dict>
  <key>StartInterval</key><integer>900</integer>
  <key>StandardOutPath</key><string>${LOG_DIR}/tick.log</string>
  <key>StandardErrorPath</key><string>${LOG_DIR}/tick.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "已安装 ${LABEL}（armed=${ARMED}），日志：${LOG_DIR}/tick.log"
