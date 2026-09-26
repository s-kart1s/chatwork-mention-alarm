#!/bin/bash
set -euo pipefail

app_dir="$(cd "$(dirname "$0")" && pwd)"
exec /usr/bin/python3 "$app_dir/chatwork_alarm.py" --test-alarm --foreground
