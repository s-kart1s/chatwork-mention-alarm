import io
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from chatwork_alarm import (
    ChatworkClient,
    Alarm,
    Config,
    applescript_escape,
    is_active_hour,
    is_mention,
    is_message_in_active_hours,
    latest_message_id,
    load_config,
    load_state,
    newer_messages,
    process_messages,
    save_state,
)


class AppleScriptTests(unittest.TestCase):
    def test_escapes_backslashes_and_quotes(self):
        self.assertEqual(applescript_escape('sender\\name"'), r'sender\\name\"')


class MentionTests(unittest.TestCase):
    def test_to_is_detected(self):
        self.assertTrue(is_mention("[To:123]かーちすさん", 123))

    def test_reply_is_detected(self):
        self.assertTrue(is_mention("[rp aid=123 to=456-789]", 123))

    def test_other_user_is_ignored(self):
        self.assertFalse(is_mention("[To:999]別の人", 123))


class ActiveHourTests(unittest.TestCase):
    def test_normal_window(self):
        self.assertTrue(is_active_hour(datetime(2026, 9, 26, 4, 0), 4, 9))
        self.assertTrue(is_active_hour(datetime(2026, 9, 26, 8, 59), 4, 9))
        self.assertFalse(is_active_hour(datetime(2026, 9, 26, 9, 0), 4, 9))

    def test_overnight_window(self):
        self.assertTrue(is_active_hour(datetime(2026, 9, 26, 23, 0), 22, 6))
        self.assertTrue(is_active_hour(datetime(2026, 9, 26, 5, 0), 22, 6))
        self.assertFalse(is_active_hour(datetime(2026, 9, 26, 12, 0), 22, 6))

    def test_message_send_time_is_used(self):
        message = {"send_time": int(datetime(2026, 9, 26, 3, 59).timestamp())}
        self.assertFalse(is_message_in_active_hours(message, 4, 9))

        message = {"send_time": int(datetime(2026, 9, 26, 4, 0).timestamp())}
        self.assertTrue(is_message_in_active_hours(message, 4, 9))


class MessageTests(unittest.TestCase):
    def setUp(self):
        self.messages = [
            {"message_id": "30"},
            {"message_id": "10"},
            {"message_id": "20"},
        ]

    def test_latest_message_id(self):
        self.assertEqual(latest_message_id(self.messages), "30")

    def test_newer_messages_are_sorted(self):
        self.assertEqual(
            [item["message_id"] for item in newer_messages(self.messages, "10")],
            ["20", "30"],
        )

    def test_first_run_returns_no_messages(self):
        self.assertEqual(newer_messages(self.messages, None), [])


class StateTests(unittest.TestCase):
    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_state(path, {"123": "456"})
            self.assertEqual(load_state(path), {"123": "456"})

    def test_invalid_state_rebuilds_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("invalid", encoding="utf-8")
            self.assertEqual(load_state(path), {})


class ConfigTests(unittest.TestCase):
    def write_config(self, directory, **overrides):
        config = {
            "room_ids": [123],
            "start_hour": 4,
            "end_hour": 9,
            "poll_seconds": 30,
            "sound_path": "/System/Library/Sounds/Sosumi.aiff",
            "sound_volume": 2.0,
            **overrides,
        }
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def test_rejects_excessive_api_rate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(
                directory,
                room_ids=list(range(20)),
                poll_seconds=15,
            )
            with self.assertRaisesRegex(ValueError, "request rate"):
                load_config(path)

    def test_rejects_invalid_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, sound_volume=float("nan"))
            with self.assertRaisesRegex(ValueError, "sound_volume"):
                load_config(path)

    def test_default_poll_interval_is_sixty_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory)
            raw = json.loads(path.read_text(encoding="utf-8"))
            del raw["poll_seconds"]
            path.write_text(json.dumps(raw), encoding="utf-8")
            self.assertEqual(load_config(path).poll_seconds, 60)


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status = status
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


class ChatworkClientTests(unittest.TestCase):
    def test_no_content_returns_empty_list(self):
        with patch("chatwork_alarm.urlopen", return_value=FakeResponse(204)):
            self.assertEqual(ChatworkClient("token").recent_messages(123), [])

    def test_http_error_does_not_expose_token(self):
        error = HTTPError(
            "https://api.chatwork.com/v2/me",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"errors":["Invalid token"]}'),
        )
        with patch("chatwork_alarm.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "Chatwork API error 401") as raised:
                ChatworkClient("secret-token").me()
        self.assertNotIn("secret-token", str(raised.exception))

    def test_connection_error_is_wrapped(self):
        with patch("chatwork_alarm.urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(RuntimeError, "connection error"):
                ChatworkClient("token").me()


class AlarmTests(unittest.TestCase):
    def test_osascript_failure_is_reported(self):
        process = Mock(returncode=1)
        process.poll.return_value = 1
        with patch("chatwork_alarm.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "osascript exited"):
                Alarm("/sound", 1).run("room", "sender", "url")


class ProcessMessagesTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(
            room_ids=(123,),
            start_hour=4,
            end_hour=9,
            poll_seconds=30,
            sound_path="/System/Library/Sounds/Sosumi.aiff",
            sound_volume=2,
        )
        self.message = {
            "message_id": "20",
            "send_time": int(datetime(2026, 9, 26, 5, 0).timestamp()),
            "body": "[To:123] wake up",
            "account": {"name": "sender"},
        }

    def test_returns_latest_after_alarm_is_acknowledged(self):
        alarm = Mock()
        self.assertEqual(
            process_messages([self.message], "10", 123, self.config, alarm, 456),
            "20",
        )
        alarm.run.assert_called_once()

    def test_does_not_return_latest_when_alarm_fails(self):
        alarm = Mock()
        alarm.run.side_effect = RuntimeError("alarm failed")
        with self.assertRaisesRegex(RuntimeError, "alarm failed"):
            process_messages([self.message], "10", 123, self.config, alarm, 456)

    def test_empty_room_gets_zero_baseline(self):
        alarm = Mock()
        self.assertEqual(
            process_messages([], None, 123, self.config, alarm, 456),
            "0",
        )
        alarm.run.assert_not_called()

    def test_first_message_after_empty_baseline_can_alert(self):
        alarm = Mock()
        self.assertEqual(
            process_messages([self.message], "0", 123, self.config, alarm, 456),
            "20",
        )
        alarm.run.assert_called_once()

    def test_existing_messages_create_baseline_without_alert(self):
        alarm = Mock()
        self.assertEqual(
            process_messages([self.message], None, 123, self.config, alarm, 456),
            "20",
        )
        alarm.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
