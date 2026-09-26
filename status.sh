#!/bin/bash
set -euo pipefail

service="io.github.skartis.chatwork-mention-alarm"
log_file="$HOME/Library/Logs/chatwork-mention-alarm/alarm.log"

if /bin/launchctl print "gui/$(id -u)/$service" >/dev/null 2>&1; then
  echo "監視状態: 稼働中"
else
  echo "監視状態: 停止中"
fi

if [[ -f "$log_file" ]]; then
  echo
  echo "直近のログ:"
  tail -n 15 "$log_file"
fi
