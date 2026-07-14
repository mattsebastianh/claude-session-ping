import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telegram_qa_lib import (
    WINDOW_SECONDS,
    format_time,
    match_intent,
    next_start_times,
    parse_env_text,
    usage_percent,
    window_end,
)


class TestUsagePercent(unittest.TestCase):
    def test_half_elapsed(self):
        now = 1_000_000
        window_start = now - WINDOW_SECONDS // 2
        self.assertAlmostEqual(usage_percent(window_start, now), 50.0)

    def test_just_started(self):
        now = 1_000_000
        self.assertEqual(usage_percent(now, now), 0.0)

    def test_clamped_at_100(self):
        now = 1_000_000
        window_start = now - WINDOW_SECONDS * 2
        self.assertEqual(usage_percent(window_start, now), 100.0)

    def test_no_window_start(self):
        self.assertEqual(usage_percent(0, 1_000_000), 0.0)


class TestWindowEnd(unittest.TestCase):
    def test_adds_five_hours(self):
        self.assertEqual(window_end(1_000_000), 1_000_000 + WINDOW_SECONDS)


class TestNextStartTimes(unittest.TestCase):
    def test_returns_next_two_in_order(self):
        now = int(datetime.datetime(2026, 7, 13, 8, 0, 0).timestamp())
        starts = next_start_times(now)
        self.assertEqual(len(starts), 2)
        self.assertEqual(format_time(starts[0]), "09:00")
        self.assertEqual(format_time(starts[1]), "14:00")

    def test_rolls_over_to_next_day(self):
        now = int(datetime.datetime(2026, 7, 13, 20, 0, 0).timestamp())
        starts = next_start_times(now)
        self.assertEqual(format_time(starts[0]), "04:00")
        self.assertEqual(format_time(starts[1]), "09:00")


class TestMatchIntent(unittest.TestCase):
    def test_usage(self):
        self.assertEqual(match_intent("what's my usage %?"), "usage")

    def test_window_end(self):
        self.assertEqual(match_intent("when does this window end"), "window_end")

    def test_next_start(self):
        self.assertEqual(match_intent("what's the next session start time?"), "next_start")

    def test_next_next_start(self):
        self.assertEqual(match_intent("what about the next next one"), "next_next_start")

    def test_none(self):
        self.assertEqual(match_intent("tell me a joke"), "none")

    def test_weekend_does_not_match_window_end(self):
        self.assertEqual(match_intent("anything happening this weekend?"), "none")

    def test_recover_does_not_match_window_end(self):
        self.assertEqual(match_intent("did you recover the file"), "none")

    def test_friend_does_not_match_window_end(self):
        self.assertEqual(match_intent("I have a friend"), "none")


class TestParseEnvText(unittest.TestCase):
    def test_parses_simple_pairs(self):
        text = """
        # comment
        TELEGRAM_BOT_TOKEN=abc123

        TELEGRAM_CHAT_ID='999'
        """
        env = parse_env_text(text)
        self.assertEqual(env["TELEGRAM_BOT_TOKEN"], "abc123")
        self.assertEqual(env["TELEGRAM_CHAT_ID"], "999")


if __name__ == "__main__":
    unittest.main()
