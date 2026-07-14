import datetime
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import telegram_qa_daemon as daemon
from telegram_qa_lib import WINDOW_SECONDS

EMPTY_STATE = {"window_start": 0, "window_label": "unknown", "status": "unknown"}


class TestAnswerQuestion(unittest.TestCase):
    def test_usage_with_no_state_infers_window_from_schedule(self):
        # 22:07 — inside the 19:00 window (5h => ends 00:00), 62% elapsed.
        now = int(datetime.datetime(2026, 7, 13, 22, 7, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "what's my session usage")
        self.assertEqual(reply, "Current window (opened 19:00) is 62% elapsed, ends around 00:00.")

    def test_usage_with_no_state_outside_any_window(self):
        now = int(datetime.datetime(2026, 7, 13, 2, 30, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "whats my usage")
        self.assertEqual(reply, "No session window is active right now. Next one starts at 04:00.")

    def test_window_end_with_no_state_infers_window_from_schedule(self):
        now = int(datetime.datetime(2026, 7, 13, 22, 7, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "when does this window end")
        self.assertEqual(reply, "Current window ends around 00:00 (1h 53m left).")

    def test_window_open_with_no_state_infers_from_schedule(self):
        now = int(datetime.datetime(2026, 7, 13, 22, 7, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "when this current window opened?")
        self.assertEqual(reply, "Current window opened at 19:00 (3h 7m ago).")

    def test_window_open_outside_any_window(self):
        now = int(datetime.datetime(2026, 7, 13, 2, 30, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "when did this window open")
        self.assertEqual(reply, "No session window is active right now. Next one starts at 04:00.")

    def test_next_start_includes_countdown(self):
        now = int(datetime.datetime(2026, 7, 13, 22, 7, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "when is the next reset")
        self.assertEqual(reply, "Next session window starts at 04:00 (in 5h 53m).")

    def test_next_next_start_includes_countdown(self):
        now = int(datetime.datetime(2026, 7, 13, 22, 7, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "and the one after that?")
        self.assertEqual(reply, "The session window after next starts at 09:00 (in 10h 53m).")

    def test_usage_with_tracked_window_reports_percent(self):
        window_start = int(datetime.datetime(2026, 7, 13, 9, 0, 0).timestamp())
        state = {"window_start": window_start, "window_label": "09:00", "status": "success"}
        now = window_start + WINDOW_SECONDS // 2
        with patch.object(daemon, "load_state", return_value=state), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "whats my usage")
        self.assertEqual(reply, "Current window (opened 09:00) is 50% elapsed, ends around 14:00.")

    def test_stale_tracked_window_falls_back_to_schedule(self):
        # State says 09:00 but it's 22:07 — that window closed at 14:00.
        window_start = int(datetime.datetime(2026, 7, 13, 9, 0, 0).timestamp())
        state = {"window_start": window_start, "window_label": "09:00", "status": "success"}
        now = int(datetime.datetime(2026, 7, 13, 22, 7, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=state), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "whats my usage")
        self.assertEqual(reply, "Current window (opened 19:00) is 62% elapsed, ends around 00:00.")


class TestFallbackPaths(unittest.TestCase):
    def test_unrecognized_question_without_api_key(self):
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE):
            reply = daemon.answer_question({}, "tell me a joke")
        self.assertEqual(reply, "I don't recognize that question and no OPENAI_API_KEY is configured.")

    def test_openai_network_error_returns_friendly_message(self):
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")), \
                patch.object(daemon, "log"):
            reply = daemon.openai_answer("sk-test", "gpt-5-nano", EMPTY_STATE, "hi")
        self.assertEqual(reply, "Sorry, I couldn't reach the answering service right now.")

    def test_openai_response_without_message_returns_friendly_message(self):
        import io

        fake = io.BytesIO(b'{"output": [{"type": "reasoning"}]}')
        fake.__enter__ = lambda s: s
        fake.__exit__ = lambda s, *a: False
        with patch("urllib.request.urlopen", return_value=fake), patch.object(daemon, "log"):
            reply = daemon.openai_answer("sk-test", "gpt-5-nano", EMPTY_STATE, "hi")
        self.assertEqual(reply, "Sorry, I couldn't reach the answering service right now.")


if __name__ == "__main__":
    unittest.main()
