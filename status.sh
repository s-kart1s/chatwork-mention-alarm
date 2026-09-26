#!/bin/bash
set -euo pipefail

service="io.github.skartis.chatwork-mention-alarm"
log_file="$HOME/Library/Logs/chatwork-mention-alarm/alarm.log"

service_status="$(/bin/launchctl print "gui/$(id -u)/$service" 2>/dev/null || true)"
if /usr/bin/grep -q 'state = running' <<<"$service_status"; then
  echo "監視状態: 稼働中"
  exit_code=0
elif [[ -n "$service_status" ]]; then
  echo "監視状態: 異常（登録済みですが稼働していません）"
  /usr/bin/grep -E 'state =|last exit code =|last terminating signal =' <<<"$service_status" || true
  exit_code=1
else
  echo "監視状態: 停止中"
  exit_code=1
fi

if [[ -f "$log_file" ]]; then
  echo
  echo "直近のログ:"
  tail -n 15 "$log_file"
fi

exit "$exit_code"
