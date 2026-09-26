#!/bin/bash
set -euo pipefail

service="io.github.skartis.chatwork-mention-alarm"
agent_path="$HOME/Library/LaunchAgents/$service.plist"
runtime_dir="$HOME/Library/Application Support/chatwork-mention-alarm"

/bin/launchctl bootout "gui/$(id -u)/$service" 2>/dev/null || true
if [[ -f "$agent_path" ]]; then
  mv "$agent_path" "$HOME/.Trash/$service.plist.$(date +%Y%m%d%H%M%S)"
fi
if [[ -d "$runtime_dir" ]]; then
  mv "$runtime_dir" "$HOME/.Trash/chatwork-mention-alarm-runtime.$(date +%Y%m%d%H%M%S)"
fi

echo "常駐監視を停止し、LaunchAgentと実行ファイルをゴミ箱へ移動しました。"
echo "設定・ログ・キーチェーンのトークンは、再導入できるよう残しています。"
