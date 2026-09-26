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
python_command="$(command -v python3 || true)"

if [[ -z "$python_command" ]]; then
  echo "python3 が見つかりません。Python 3をインストールしてから再実行してください。" >&2
  exit 1
fi
python_bin="$("$python_command" -c 'import sys; print(sys.executable)')"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "このツールはmacOS専用です。" >&2
  exit 1
fi

default_room_ids=""
default_start_hour="4"
default_end_hour="9"
default_poll_seconds="60"
existing_config="$config_dir/config.json"
if [[ -f "$existing_config" ]]; then
  IFS='|' read -r \
    default_room_ids default_start_hour default_end_hour default_poll_seconds \
    < <("$python_bin" - "$existing_config" <<'PY'
import json
from pathlib import Path
import sys

try:
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    room_ids = ",".join(str(value) for value in config["room_ids"])
    print(
        room_ids,
        config.get("start_hour", 4),
        config.get("end_hour", 9),
        config.get("poll_seconds", 60),
        sep="|",
    )
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    print("", 4, 9, 60, sep="|")
PY
  )
fi

if [[ -n "$default_room_ids" ]]; then
  room_prompt="監視するChatworkルームID（複数可・カンマ区切り）[$default_room_ids]: "
else
  room_prompt="監視するChatworkルームID（URLのrid直後の数字、複数可・カンマ区切り）: "
fi
read -r -p "$room_prompt" room_ids
room_ids="${room_ids:-$default_room_ids}"
if [[ ! "$room_ids" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
  echo "ルームIDは数字をカンマ区切りで入力してください。" >&2
  exit 1
fi

read -r -p "監視開始時刻（0〜23）[$default_start_hour]: " start_hour
start_hour="${start_hour:-$default_start_hour}"
read -r -p "監視終了時刻（0〜23）[$default_end_hour]: " end_hour
end_hour="${end_hour:-$default_end_hour}"
read -r -p "確認間隔（秒、15以上）[$default_poll_seconds]: " poll_seconds
poll_seconds="${poll_seconds:-$default_poll_seconds}"

if [[ ! "$start_hour" =~ ^([0-9]|1[0-9]|2[0-3])$ ]] || \
   [[ ! "$end_hour" =~ ^([0-9]|1[0-9]|2[0-3])$ ]] || \
   [[ ! "$poll_seconds" =~ ^[0-9]+$ ]] || (( poll_seconds < 15 )); then
  echo "時刻または確認間隔の値が不正です。" >&2
  exit 1
fi

temporary_dir="$(mktemp -d)"
trap 'rm -rf "$temporary_dir"' EXIT
temporary_config="$temporary_dir/config.json"
temporary_log="$temporary_dir/check.log"

created_token=0
if /usr/bin/security find-generic-password -s "$service" -a "$keychain_account" >/dev/null 2>&1; then
  echo "キーチェーンに保存済みのChatwork APIトークンを使用します。"
else
  echo "この後、macOSキーチェーンが「password data」と「retype password」を尋ねます。"
  echo "どちらにも同じChatwork APIトークンを入力してください（画面には表示されません）。"
  /usr/bin/security add-generic-password \
    -s "$service" \
    -a "$keychain_account" \
    -w >/dev/null
  created_token=1
fi

"$python_bin" - "$temporary_config" "$room_ids" "$start_hour" "$end_hour" "$poll_seconds" <<'PY'
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

echo "Chatwork APIへの接続と設定を確認しています。"
if ! "$python_bin" "$app_dir/chatwork_alarm.py" \
  --check \
  --config "$temporary_config" \
  --log "$temporary_log" \
  --foreground; then
  if (( created_token )); then
    /usr/bin/security delete-generic-password \
      -s "$service" \
      -a "$keychain_account" >/dev/null 2>&1 || true
    echo "今回入力したAPIトークンをキーチェーンから削除しました。" >&2
  fi
  echo "API接続または設定の確認に失敗しました。常駐監視は開始していません。" >&2
  exit 1
fi

mkdir -p "$config_dir" "$runtime_dir" "$log_dir" "$HOME/Library/LaunchAgents"
cp "$temporary_config" "$config_dir/config.json"
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

first_pid=""
for _ in {1..10}; do
  service_status="$(/bin/launchctl print "$domain/$service" 2>/dev/null || true)"
  if /usr/bin/grep -q 'state = running' <<<"$service_status"; then
    first_pid="$(/usr/bin/awk '/pid =/{print $3; exit}' <<<"$service_status")"
    break
  fi
  sleep 1
done

if [[ -n "$first_pid" ]]; then
  sleep 2
  service_status="$(/bin/launchctl print "$domain/$service" 2>/dev/null || true)"
  second_pid="$(/usr/bin/awk '/pid =/{print $3; exit}' <<<"$service_status")"
  if /usr/bin/grep -q 'state = running' <<<"$service_status" && [[ "$first_pid" == "$second_pid" ]]; then
    echo "Chatworkメンション監視を開始しました。"
    echo "状態確認: $app_dir/status.sh"
    echo "アラーム試験: $app_dir/test-alarm.sh"
    exit 0
  fi
fi

echo "常駐プロセスの起動を確認できませんでした。" >&2
tail -n 20 "$log_dir/launchd.err.log" 2>/dev/null || true
exit 1
