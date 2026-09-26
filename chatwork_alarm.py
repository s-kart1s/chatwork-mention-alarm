#!/usr/bin/env python3
"""Poll Chatwork for early-morning mentions and sound a local macOS alarm."""

from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


APP_NAME = "chatwork-mention-alarm"
KEYCHAIN_SERVICE = "io.github.skartis.chatwork-mention-alarm"
KEYCHAIN_ACCOUNT = "chatwork-api-token"
API_BASE = "https://api.chatwork.com/v2"
DEFAULT_CONFIG = Path.home() / ".config" / APP_NAME / "config.json"
DEFAULT_STATE = Path.home() / "Library" / "Application Support" / APP_NAME / "state.json"
DEFAULT_LOG = Path.home() / "Library" / "Logs" / APP_NAME / "alarm.log"
DEFAULT_SOUND = "/System/Library/Sounds/Sosumi.aiff"


@dataclass(frozen=True)
class Config:
    room_ids: tuple[int, ...]
    start_hour: int
    end_hour: int
    poll_seconds: int
    sound_path: str
    sound_volume: float


class ChatworkClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def _get(self, path: str) -> Any:
        request = Request(
            f"{API_BASE}{path}",
            headers={"x-chatworktoken": self.token, "accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=20) as response:
                if response.status == 204:
                    return []
                return json.load(response)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Chatwork API error {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Chatwork API connection error: {exc.reason}") from exc

    def me(self) -> dict[str, Any]:
        return self._get("/me")

    def recent_messages(self, room_id: int) -> list[dict[str, Any]]:
        return self._get(f"/rooms/{room_id}/messages?force=1")


def read_token() -> str:
    result = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            KEYCHAIN_ACCOUNT,
            "-w",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            "Chatwork API token is not in Keychain. Run install.sh first."
        )
    return result.stdout.strip()


def load_config(path: Path) -> Config:
    raw = json.loads(path.read_text(encoding="utf-8"))
    room_ids = tuple(int(value) for value in raw["room_ids"])
    if not room_ids:
        raise ValueError("room_ids must not be empty")
    start_hour = int(raw.get("start_hour", 4))
    end_hour = int(raw.get("end_hour", 9))
    poll_seconds = int(raw.get("poll_seconds", 30))
    if not 0 <= start_hour <= 23 or not 0 <= end_hour <= 23:
        raise ValueError("start_hour and end_hour must be between 0 and 23")
    if poll_seconds < 15:
        raise ValueError("poll_seconds must be at least 15")
    requests_per_five_minutes = len(room_ids) * 300 / poll_seconds
    if requests_per_five_minutes > 280:
        raise ValueError(
            "room_ids and poll_seconds exceed the safe Chatwork API request rate"
        )
    sound_path = str(raw.get("sound_path", DEFAULT_SOUND))
    if not Path(sound_path).is_file():
        raise ValueError(f"sound_path does not exist: {sound_path}")
    sound_volume = float(raw.get("sound_volume", 2.0))
    if not math.isfinite(sound_volume) or not 0 <= sound_volume <= 2:
        raise ValueError("sound_volume must be between 0 and 2")
    return Config(
        room_ids=room_ids,
        start_hour=start_hour,
        end_hour=end_hour,
        poll_seconds=poll_seconds,
        sound_path=sound_path,
        sound_volume=sound_volume,
    )


def is_active_hour(now: datetime, start_hour: int, end_hour: int) -> bool:
    hour = now.hour
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def is_mention(body: str, account_id: int) -> bool:
    return f"[To:{account_id}]" in body or f"[rp aid={account_id} " in body


def is_message_in_active_hours(
    message: dict[str, Any], start_hour: int, end_hour: int
) -> bool:
    sent_at = datetime.fromtimestamp(int(message["send_time"]))
    return is_active_hour(sent_at, start_hour, end_hour)


def load_state(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {str(key): str(value) for key, value in raw.items()}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, TypeError, ValueError):
        logging.warning("State file was invalid; rebuilding the baseline")
        return {}


def save_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def newer_messages(
    messages: list[dict[str, Any]], last_message_id: str | None
) -> list[dict[str, Any]]:
    ordered = sorted(messages, key=lambda item: int(item["message_id"]))
    if last_message_id is None:
        return []
    return [item for item in ordered if int(item["message_id"]) > int(last_message_id)]


def latest_message_id(messages: list[dict[str, Any]]) -> str | None:
    if not messages:
        return None
    return str(max(int(item["message_id"]) for item in messages))


class Alarm:
    def __init__(self, sound_path: str, volume: float) -> None:
        self.sound_path = sound_path
        self.volume = volume

    def run(self, room_name: str, sender: str, message_url: str) -> None:
        title = "Chatwork 緊急メンション"
        message = f"{sender}さんからTo/返信があります。\nルーム: {room_name}\n{message_url}"
        script = (
            'display dialog "' + applescript_escape(message) + '" '
            'with title "' + applescript_escape(title) + '" '
            'buttons {"停止"} default button "停止" with icon caution'
        )
        dialog: subprocess.Popen[bytes] | None = None
        player: subprocess.Popen[bytes] | None = None
        try:
            dialog = subprocess.Popen(["/usr/bin/osascript", "-e", script])
            while dialog.poll() is None:
                started_at = time.monotonic()
                player = subprocess.Popen(
                    [
                        "/usr/bin/afplay",
                        "-v",
                        str(self.volume),
                        self.sound_path,
                    ]
                )
                while player.poll() is None and dialog.poll() is None:
                    time.sleep(0.2)
                if dialog.poll() is not None and player.poll() is None:
                    player.terminate()
                    player.wait(timeout=2)
                elif player.returncode:
                    raise RuntimeError(f"afplay exited with status {player.returncode}")
                elif time.monotonic() - started_at < 0.1:
                    time.sleep(1)
            if dialog.returncode:
                raise RuntimeError(f"osascript exited with status {dialog.returncode}")
        finally:
            terminate_process(player)
            terminate_process(dialog)
            logging.info("Alarm stopped")


def applescript_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def terminate_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def handle_shutdown(_signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt


def configure_logging(log_path: Path, foreground: bool) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
    ]
    if foreground:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def check_connection(config: Config) -> None:
    client = ChatworkClient(read_token())
    client.me()
    for room_id in config.room_ids:
        client.recent_messages(room_id)
    logging.info("Connection check succeeded for %s room(s)", len(config.room_ids))


def process_messages(
    messages: list[dict[str, Any]],
    previous: str | None,
    account_id: int,
    config: Config,
    alarm: Alarm,
    room_id: int,
) -> str | None:
    latest = latest_message_id(messages)
    if latest is None:
        return None
    if previous is None:
        logging.info("Baseline set for room %s", room_id)
        return latest

    mentions = [
        message
        for message in newer_messages(messages, previous)
        if is_mention(str(message.get("body", "")), account_id)
        and is_message_in_active_hours(message, config.start_hour, config.end_hour)
    ]
    if mentions:
        message = mentions[0]
        sender = str(message.get("account", {}).get("name", "不明"))
        message_id = str(message["message_id"])
        url = f"https://www.chatwork.com/#!rid{room_id}-{message_id}"
        logging.warning(
            "Mention detected: room=%s sender=%s message_id=%s count=%s",
            room_id,
            sender,
            message_id,
            len(mentions),
        )
        alarm.run(str(room_id), sender, url)
    return latest


def run(config: Config, state_path: Path) -> None:
    token = read_token()
    client = ChatworkClient(token)
    profile = client.me()
    account_id = int(profile["account_id"])
    logging.info("Watcher started for account_id=%s", account_id)

    alarm = Alarm(config.sound_path, config.sound_volume)
    state = load_state(state_path)

    while True:
        for room_id in config.room_ids:
            try:
                messages = client.recent_messages(room_id)
                room_key = str(room_id)
                latest = process_messages(
                    messages,
                    state.get(room_key),
                    account_id,
                    config,
                    alarm,
                    room_id,
                )
                if latest is None:
                    continue
                state[room_key] = latest
                save_state(state_path, state)
            except Exception:
                logging.exception("Failed to check room %s", room_id)
        time.sleep(config.poll_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the configuration and Chatwork API access, then exit",
    )
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument(
        "--test-alarm",
        action="store_true",
        help="Sound the alarm without contacting Chatwork",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.log, args.foreground)
    signal.signal(signal.SIGTERM, handle_shutdown)
    if args.test_alarm:
        config = load_config(args.config)
        alarm = Alarm(config.sound_path, config.sound_volume)
        alarm.run("動作確認", "Chatwork Alarm", "テストです")
        return 0
    try:
        config = load_config(args.config)
        if args.check:
            check_connection(config)
        else:
            run(config, args.state)
    except KeyboardInterrupt:
        logging.info("Watcher stopped")
    except Exception:
        logging.exception("Watcher terminated")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
