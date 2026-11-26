#!/usr/bin/env bash
# 交互式配置 mooncode-bot 的两个 GitHub Secret。密钥只在终端里隐藏输入，不会出现在聊天、日志或 shell 历史里。
#
#   ./scripts/setup-secrets.sh            # 两个都配
#   ./scripts/setup-secrets.sh github     # 只配 MOONCODE_TOKEN
#   ./scripts/setup-secrets.sh kimi       # 只配 KIMI_CONFIG_TOML
set -euo pipefail

REPO="qkunio/mooncode-bot"
WHAT="${1:-all}"

setup_github() {
  echo "== MOONCODE_TOKEN =="
  echo "1) 浏览器打开下面的链接，创建 Fine-grained Token（权限已预填，只需确认仓库选 qkunio/mooncode）："
  echo "   https://github.com/settings/personal-access-tokens/new?name=mooncode-bot&description=mooncode-bot%20push%2FPR%2Ftag%20to%20qkunio%2Fmooncode&target_name=qkunio&expires_in=365&contents=write&pull_requests=write&actions=read"
  echo "   Repository access 选 Only select repositories → qkunio/mooncode"
  echo "2) 复制生成的 github_pat_... 粘贴到下面（输入不可见）"
  read -r -s -p "Token: " TOKEN; echo
  [ -n "${TOKEN}" ] || { echo "为空，跳过"; return; }
  printf '%s' "${TOKEN}" | gh secret set MOONCODE_TOKEN -R "${REPO}"
  unset TOKEN
}

setup_kimi() {
  echo "== KIMI_CONFIG_TOML =="
  echo "Key 来源：1) Kimi Code 会员 API Key（api.kimi.com/coding）  2) Moonshot 开放平台 Key（api.moonshot.cn）"
  read -r -p "选择 [1/2，默认 1]: " SRC
  if [ "${SRC:-1}" = "2" ]; then
    BASE_URL="https://api.moonshot.cn/v1"
    read -r -p "模型名 [默认 kimi-k2-turbo-preview]: " MODEL; MODEL="${MODEL:-kimi-k2-turbo-preview}"
  else
    BASE_URL="https://api.kimi.com/coding/v1"
    read -r -p "模型名 [默认 k3.1-pro]: " MODEL; MODEL="${MODEL:-k3.1-pro}"
  fi
  read -r -s -p "API Key（输入不可见）: " KEY; echo
  [ -n "${KEY}" ] || { echo "为空，跳过"; return; }
  cat <<EOF | gh secret set KIMI_CONFIG_TOML -R "${REPO}"
default_model = "bot/${MODEL}"
default_thinking = true
default_permission_mode = "auto"
telemetry = false
disable_feedback_survey = true

[providers.bot]
type = "kimi"
base_url = "${BASE_URL}"
api_key = "${KEY}"

[models."bot/${MODEL}"]
provider = "bot"
model = "${MODEL}"
max_context_size = 1048576
capabilities = ["thinking", "always_thinking", "image_in", "video_in", "tool_use", "dynamically_loaded_tools"]
support_efforts = ["low", "high", "max"]
default_effort = "high"
EOF
  unset KEY
}

case "${WHAT}" in
  github) setup_github ;;
  kimi) setup_kimi ;;
  *) setup_github; setup_kimi ;;
esac

echo
gh secret list -R "${REPO}"
