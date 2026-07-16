import datetime
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import telegram_qa_daemon as daemon
from telegram_qa_lib import WINDOW_SECONDS

EMPTY_STATE = {"window_start": 0, "window_label": "unknown", "status": "unknown"}


class NoRealUsageLookup(unittest.TestCase):
    """Base for tests that answer questions via the state/schedule fallback.

    Without this, answer_question's usage lookup shells out to the real
    `claude` CLI and answers from this machine's live window instead of the
    case under test.
    """

    def setUp(self):
        usage_patch = patch.object(daemon, "get_usage", return_value=None)
        usage_patch.start()
        self.addCleanup(usage_patch.stop)


class TestAnswerQuestion(NoRealUsageLookup):
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


class TestFallbackPaths(NoRealUsageLookup):
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

    def test_get_updates_network_failure_sleeps_before_retry(self):
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")), \
                patch.object(daemon, "log") as log_mock, \
                patch("time.sleep") as sleep_mock:
            result, failed, error_message = daemon.get_updates("token", None)

        self.assertEqual(result, [])
        self.assertTrue(failed)
        self.assertEqual(error_message, "<urlopen error boom>")
        sleep_mock.assert_called_once_with(5)
        log_mock.assert_called_once()

    def test_maybe_notify_poll_failure_alerts_after_threshold(self):
        with patch.object(daemon, "send_message") as send_message_mock, \
                patch.object(daemon, "log") as log_mock:
            daemon.maybe_notify_poll_failure("token", "123", daemon.MAX_GETUPDATES_FAILURES_BEFORE_ALERT, "boom")

        expected = (
            f"Telegram polling has failed {daemon.MAX_GETUPDATES_FAILURES_BEFORE_ALERT} times in a row; "
            "last error: boom. I will notify you if it continues."
        )
        send_message_mock.assert_called_once_with("token", "123", expected)
        log_mock.assert_called_once_with(expected)

    def test_maybe_notify_poll_failure_does_not_alert_below_threshold(self):
        with patch.object(daemon, "send_message") as send_message_mock, \
                patch.object(daemon, "log") as log_mock:
            daemon.maybe_notify_poll_failure("token", "123", daemon.MAX_GETUPDATES_FAILURES_BEFORE_ALERT - 1, "boom")

        send_message_mock.assert_not_called()
        log_mock.assert_not_called()


class TestFetchUsageAndWindow(unittest.TestCase):
    def test_prefers_real_usage_over_schedule(self):
        now = int(datetime.datetime(2026, 7, 15, 14, 30, 0).timestamp())
        resets_at = int(datetime.datetime(2026, 7, 15, 19, 9, 0).timestamp())
        usage = {"session": {"pct": 5.0, "resets_at": resets_at}, "weekly": None}
        with patch.object(daemon, "get_usage", return_value=usage), \
             patch.object(daemon, "load_state", return_value=EMPTY_STATE):
            got_usage, window_start = daemon.fetch_usage_and_window(now)
        self.assertEqual(got_usage, usage)
        self.assertEqual(window_start, int(datetime.datetime(2026, 7, 15, 14, 9, 0).timestamp()))

    def test_falls_back_to_schedule_when_usage_unavailable(self):
        now = int(datetime.datetime(2026, 7, 15, 22, 7, 0).timestamp())
        with patch.object(daemon, "get_usage", return_value=None), \
             patch.object(daemon, "load_state", return_value=EMPTY_STATE):
            got_usage, window_start = daemon.fetch_usage_and_window(now)
        self.assertIsNone(got_usage)
        self.assertEqual(window_start, int(datetime.datetime(2026, 7, 15, 19, 0, 0).timestamp()))

    def test_falls_back_to_schedule_when_usage_raises(self):
        now = int(datetime.datetime(2026, 7, 15, 22, 7, 0).timestamp())
        with patch.object(daemon, "get_usage", side_effect=OSError("boom")), \
             patch.object(daemon, "load_state", return_value=EMPTY_STATE), \
             patch.object(daemon, "log"):
            got_usage, window_start = daemon.fetch_usage_and_window(now)
        self.assertIsNone(got_usage)
        self.assertEqual(window_start, int(datetime.datetime(2026, 7, 15, 19, 0, 0).timestamp()))

    def test_weekly_only_usage_keeps_schedule_window(self):
        # No session entry -> window start still comes from the schedule path.
        now = int(datetime.datetime(2026, 7, 15, 22, 7, 0).timestamp())
        usage = {"session": None, "weekly": {"pct": 41.0, "resets_at": now + 86400}}
        with patch.object(daemon, "get_usage", return_value=usage), \
             patch.object(daemon, "load_state", return_value=EMPTY_STATE):
            got_usage, window_start = daemon.fetch_usage_and_window(now)
        self.assertEqual(got_usage, usage)
        self.assertEqual(window_start, int(datetime.datetime(2026, 7, 15, 19, 0, 0).timestamp()))

    def test_next_start_answers_without_usage_lookup(self):
        # Schedule-only answers must not pay for the CLI subprocess.
        now = int(datetime.datetime(2026, 7, 15, 14, 30, 0).timestamp())
        with patch.object(daemon, "get_usage") as get_usage_mock, \
             patch.object(daemon, "load_state", return_value=EMPTY_STATE), \
             patch("time.time", return_value=now):
            daemon.answer_question({}, "when is the next reset")
        get_usage_mock.assert_not_called()

    def test_usage_answer_uses_real_window(self):
        now = int(datetime.datetime(2026, 7, 15, 16, 39, 0).timestamp())
        resets_at = int(datetime.datetime(2026, 7, 15, 19, 9, 0).timestamp())
        usage = {"session": {"pct": 50.0, "resets_at": resets_at}, "weekly": None}
        with patch.object(daemon, "get_usage", return_value=usage), \
             patch.object(daemon, "load_state", return_value=EMPTY_STATE), \
             patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "when does this window end")
        self.assertIn("19:09", reply)


if __name__ == "__main__":
    unittest.main()
