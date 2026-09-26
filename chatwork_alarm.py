#!/usr/bin/env python3
"""Poll Chatwork for early-morning mentions and sound a local macOS alarm."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
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
    test_mode: bool = False


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

    def room(self, room_id: int) -> dict[str, Any]:
        return self._get(f"/rooms/{room_id}")

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


def load_config(path: Path, *, test_mode: bool = False) -> Config:
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
    sound_path = str(raw.get("sound_path", DEFAULT_SOUND))
    if not Path(sound_path).is_file():
        raise ValueError(f"sound_path does not exist: {sound_path}")
    return Config(
        room_ids=room_ids,
        start_hour=start_hour,
        end_hour=end_hour,
        poll_seconds=poll_seconds,
        sound_path=sound_path,
        sound_volume=float(raw.get("sound_volume", 2.0)),
        test_mode=test_mode,
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
        self._lock = threading.Lock()
        self._active = False

    def trigger(self, room_name: str, sender: str, message_url: str) -> None:
        with self._lock:
            if self._active:
                logging.warning("Alarm is already active; additional mention: %s", message_url)
                return
            self._active = True
        thread = threading.Thread(
            target=self._run,
            args=(room_name, sender, message_url),
            daemon=True,
        )
        thread.start()

    def _run(self, room_name: str, sender: str, message_url: str) -> None:
        title = "Chatwork 緊急メンション"
        message = f"{sender}さんからTo/返信があります。\\nルーム: {room_name}\\n{message_url}"
        script = (
            'display dialog "' + applescript_escape(message) + '" '
            'with title "' + applescript_escape(title) + '" '
            'buttons {"停止"} default button "停止" with icon caution giving up after 3600'
        )
        dialog = subprocess.Popen(["/usr/bin/osascript", "-e", script])
        try:
            while dialog.poll() is None:
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
        finally:
            if dialog.poll() is None:
                dialog.terminate()
            with self._lock:
                self._active = False
            logging.info("Alarm stopped")


def applescript_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def configure_logging(log_path: Path, foreground: bool) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(log_path, encoding="utf-8")]
    if foreground:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def run(config: Config, state_path: Path, *, once: bool = False) -> None:
    token = read_token()
    client = ChatworkClient(token)
    profile = client.me()
    account_id = int(profile["account_id"])
    logging.info("Watcher started for account_id=%s", account_id)

    room_names: dict[int, str] = {}
    for room_id in config.room_ids:
        room_names[room_id] = str(client.room(room_id).get("name", room_id))

    alarm = Alarm(config.sound_path, config.sound_volume)
    state = load_state(state_path)

    while True:
        active = config.test_mode or is_active_hour(
            datetime.now(), config.start_hour, config.end_hour
        )
        for room_id in config.room_ids:
            try:
                messages = client.recent_messages(room_id)
                room_key = str(room_id)
                latest = latest_message_id(messages)
                if latest is None:
                    continue
                previous = state.get(room_key)
                if previous is None:
                    state[room_key] = latest
                    save_state(state_path, state)
                    logging.info("Baseline set for room %s", room_id)
                    continue
                for message in newer_messages(messages, previous):
                    body = str(message.get("body", ""))
                    if active and is_mention(body, account_id):
                        sender = str(message.get("account", {}).get("name", "不明"))
                        message_id = str(message["message_id"])
                        url = f"https://www.chatwork.com/#!rid{room_id}-{message_id}"
                        logging.warning(
                            "Mention detected: room=%s sender=%s message_id=%s",
                            room_id,
                            sender,
                            message_id,
                        )
                        alarm.trigger(room_names[room_id], sender, url)
                state[room_key] = latest
                save_state(state_path, state)
            except Exception:
                logging.exception("Failed to check room %s", room_id)
        if once:
            return
        time.sleep(config.poll_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--once", action="store_true")
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
    if args.test_alarm:
        config = load_config(args.config, test_mode=True)
        alarm = Alarm(config.sound_path, config.sound_volume)
        alarm.trigger("動作確認", "Chatwork Alarm", "テストです")
        while alarm._active:
            time.sleep(0.2)
        return 0
    try:
        config = load_config(args.config)
        run(config, args.state, once=args.once)
    except KeyboardInterrupt:
        logging.info("Watcher stopped")
    except Exception:
        logging.exception("Watcher terminated")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
