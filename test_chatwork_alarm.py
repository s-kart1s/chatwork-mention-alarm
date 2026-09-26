import unittest
from datetime import datetime

from chatwork_alarm import (
    is_active_hour,
    is_mention,
    is_message_in_active_hours,
    latest_message_id,
    newer_messages,
)


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


if __name__ == "__main__":
    unittest.main()
