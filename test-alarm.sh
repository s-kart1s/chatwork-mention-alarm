#!/bin/bash
set -euo pipefail

app_dir="$(cd "$(dirname "$0")" && pwd)"
python_bin="$(command -v python3 || true)"
if [[ -z "$python_bin" ]]; then
  echo "python3 が見つかりません。" >&2
  exit 1
fi
exec "$python_bin" "$app_dir/chatwork_alarm.py" --test-alarm --foreground
