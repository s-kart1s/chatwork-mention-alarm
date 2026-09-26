#!/bin/bash
set -euo pipefail

app_dir="$(cd "$(dirname "$0")" && pwd)"
app_name="chatwork-mention-alarm"
service="io.github.skartis.chatwork-mention-alarm"
keychain_account="chatwork-api-token"
config_dir="$HOME/.config/$app_name"
runtime_dir="$HOME/Library/Application Support/$app_name"
log_dir="$HOME/Library/Logs/$app_name"
agent_path="$HOME/Library/LaunchAgents/$service.plist"
python_bin="$(command -v python3 || true)"

if [[ -z "$python_bin" ]]; then
  echo "python3 が見つかりません。Python 3をインストールしてから再実行してください。" >&2
  exit 1
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "このツールはmacOS専用です。" >&2
  exit 1
fi

read -r -p "監視するChatworkルームID（複数の場合はカンマ区切り）: " room_ids
if [[ ! "$room_ids" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
  echo "ルームIDは数字をカンマ区切りで入力してください。" >&2
  exit 1
fi

read -r -p "監視開始時刻（0〜23）[4]: " start_hour
start_hour="${start_hour:-4}"
read -r -p "監視終了時刻（0〜23）[9]: " end_hour
end_hour="${end_hour:-9}"
read -r -p "確認間隔（秒、15以上）[30]: " poll_seconds
poll_seconds="${poll_seconds:-30}"

if [[ ! "$start_hour" =~ ^([0-9]|1[0-9]|2[0-3])$ ]] || \
   [[ ! "$end_hour" =~ ^([0-9]|1[0-9]|2[0-3])$ ]] || \
   [[ ! "$poll_seconds" =~ ^[0-9]+$ ]] || (( poll_seconds < 15 )); then
  echo "時刻または確認間隔の値が不正です。" >&2
  exit 1
fi

read -r -s -p "Chatwork APIトークン（画面には表示されません）: " token
echo
if [[ -z "$token" ]]; then
  echo "APIトークンが空のため中止しました。" >&2
  exit 1
fi

mkdir -p "$config_dir" "$runtime_dir" "$log_dir" "$HOME/Library/LaunchAgents"

/usr/bin/security add-generic-password \
  -U \
  -s "$service" \
  -a "$keychain_account" \
  -w "$token" >/dev/null
unset token

"$python_bin" - "$config_dir/config.json" "$room_ids" "$start_hour" "$end_hour" "$poll_seconds" <<'PY'
import json
from pathlib import Path
import sys

path, room_ids, start_hour, end_hour, poll_seconds = sys.argv[1:]
config = {
    "room_ids": [int(value) for value in room_ids.split(",")],
    "start_hour": int(start_hour),
    "end_hour": int(end_hour),
    "poll_seconds": int(poll_seconds),
    "sound_path": "/System/Library/Sounds/Sosumi.aiff",
    "sound_volume": 2.0,
}
Path(path).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
PY
chmod 600 "$config_dir/config.json"

cp "$app_dir/chatwork_alarm.py" "$runtime_dir/chatwork_alarm.py"
chmod 700 "$runtime_dir/chatwork_alarm.py"

"$python_bin" - "$agent_path" "$service" "$python_bin" "$runtime_dir/chatwork_alarm.py" "$log_dir" <<'PY'
from pathlib import Path
import plistlib
import sys

path, service, python_bin, program, log_dir = sys.argv[1:]
plist = {
    "Label": service,
    "ProgramArguments": [python_bin, program],
    "RunAtLoad": True,
    "KeepAlive": True,
    "ThrottleInterval": 30,
    "ProcessType": "Background",
    "StandardOutPath": str(Path(log_dir) / "launchd.out.log"),
    "StandardErrorPath": str(Path(log_dir) / "launchd.err.log"),
}
with Path(path).open("wb") as handle:
    plistlib.dump(plist, handle)
PY
chmod 600 "$agent_path"

domain="gui/$(id -u)"
/bin/launchctl bootout "$domain/$service" 2>/dev/null || true
/bin/launchctl bootstrap "$domain" "$agent_path"
/bin/launchctl enable "$domain/$service"
/bin/launchctl kickstart -k "$domain/$service" >/dev/null 2>&1 &

echo "Chatworkメンション監視を開始しました。"
echo "状態確認: $app_dir/status.sh"
echo "アラーム試験: $app_dir/test-alarm.sh"
