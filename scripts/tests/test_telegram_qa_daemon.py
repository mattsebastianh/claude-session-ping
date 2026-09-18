import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
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
        # An empty agent dir, so the schedule falls back to the documented
        # TARGETS. Without this the answers would depend on whatever launchd
        # happens to have installed on the machine running the suite — and
        # would flip the moment a ping run scheduled a backup.
        self.agent_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.agent_dir.cleanup)
        agent_patch = patch.object(daemon, "AGENT_DIR", Path(self.agent_dir.name))
        agent_patch.start()
        self.addCleanup(agent_patch.stop)

    def write_agent(self, name, *hhmm):
        """Install a stub launchd plist scheduling pings at `hhmm` times."""
        entries = "".join(
            "<dict><key>Hour</key><integer>%d</integer>"
            "<key>Minute</key><integer>%d</integer></dict>"
            % tuple(int(part) for part in t.split(":"))
            for t in hhmm
        )
        path = Path(self.agent_dir.name) / name
        path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
            '<plist version="1.0"><dict>'
            "<key>Label</key><string>%s</string>"
            "<key>StartCalendarInterval</key><array>%s</array>"
            "</dict></plist>" % (path.stem, entries)
        )
        return path


class TestAnswerQuestion(NoRealUsageLookup):
    def test_usage_lookup_failed_falls_back_to_labeled_estimate(self):
        # 20:07 — inside the 17:02 window (5h => ends 22:02), 62% elapsed.
        now = int(datetime.datetime(2026, 7, 13, 20, 7, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "what's my session usage")
        self.assertEqual(
            reply,
            "⚠️ Couldn't fetch live usage. Estimate from schedule:\n"
            "📊 Session window ~62% elapsed — ends around 22:02",
        )

    def test_usage_reports_live_data_when_available(self):
        now = int(datetime.datetime(2026, 7, 16, 16, 30, 0).timestamp())  # Thu
        usage = {
            "session": {"pct": 32.0, "resets_at": int(datetime.datetime(2026, 7, 16, 19, 10, 0).timestamp())},
            "weekly": {"pct": 95.0, "resets_at": int(datetime.datetime(2026, 7, 18, 18, 0, 0).timestamp())},
        }
        with patch.object(daemon, "get_usage", return_value=usage), \
                patch.object(daemon, "load_state", return_value=EMPTY_STATE), \
                patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "what's my weekly limit?")
        self.assertEqual(
            reply,
            "📊 Session: 32% used — resets 19:10 (2h 40m left)\n"
            "📅 Weekly: 95% used — resets Sat 18:00 (2d 1h left)",
        )

    def test_usage_with_no_state_outside_any_window(self):
        now = int(datetime.datetime(2026, 7, 13, 5, 30, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "whats my usage")
        self.assertEqual(reply, "No session window is active right now. Next one starts at 07:02.")

    def test_window_end_with_no_state_infers_window_from_schedule(self):
        now = int(datetime.datetime(2026, 7, 13, 20, 7, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "when does this window end")
        self.assertEqual(reply, "Current window ends around 22:02 (1h 55m left).")

    def test_window_open_with_no_state_infers_from_schedule(self):
        now = int(datetime.datetime(2026, 7, 13, 20, 7, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "when this current window opened?")
        self.assertEqual(reply, "Current window opened at 17:02 (3h 5m ago).")

    def test_window_open_outside_any_window(self):
        now = int(datetime.datetime(2026, 7, 13, 5, 30, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "when did this window open")
        self.assertEqual(reply, "No session window is active right now. Next one starts at 07:02.")

    def test_next_start_includes_countdown(self):
        now = int(datetime.datetime(2026, 7, 13, 20, 7, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "when is the next reset")
        self.assertEqual(reply, "Next session window starts at 22:02 (in 1h 55m).")

    def test_next_next_start_includes_countdown(self):
        now = int(datetime.datetime(2026, 7, 13, 20, 7, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "and the one after that?")
        self.assertEqual(reply, "The ping after next is at 07:02 (in 10h 55m).")

    def test_usage_lookup_failed_estimates_from_tracked_window(self):
        window_start = int(datetime.datetime(2026, 7, 13, 9, 0, 0).timestamp())
        state = {"window_start": window_start, "window_label": "09:00", "status": "success"}
        now = window_start + WINDOW_SECONDS // 2
        with patch.object(daemon, "load_state", return_value=state), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "whats my usage")
        self.assertEqual(
            reply,
            "⚠️ Couldn't fetch live usage. Estimate from schedule:\n"
            "📊 Session window ~50% elapsed — ends around 14:00",
        )

    def test_usage_lookup_failed_stale_state_estimates_from_schedule(self):
        # State says 09:00 but it's 20:07 — that window closed at 14:00.
        window_start = int(datetime.datetime(2026, 7, 13, 9, 0, 0).timestamp())
        state = {"window_start": window_start, "window_label": "09:00", "status": "success"}
        now = int(datetime.datetime(2026, 7, 13, 20, 7, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=state), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "whats my usage")
        self.assertEqual(
            reply,
            "⚠️ Couldn't fetch live usage. Estimate from schedule:\n"
            "📊 Session window ~62% elapsed — ends around 22:02",
        )


class TestStatusIntent(NoRealUsageLookup):
    def test_status_question_answers_locally_without_llm(self):
        now = int(datetime.datetime(2026, 8, 25, 9, 32, 0).timestamp())
        usage = {
            "session": {"pct": 16.0, "resets_at": int(datetime.datetime(2026, 8, 25, 12, 2, 0).timestamp())},
            "weekly": {"pct": 4.0, "resets_at": int(datetime.datetime(2026, 8, 30, 0, 0, 0).timestamp())},
        }
        state = {"window_start": 0, "window_label": "07:02", "status": "success"}
        with patch.object(daemon, "get_usage", return_value=usage), \
                patch.object(daemon, "load_state", return_value=state), \
                patch("time.time", return_value=now), \
                patch.object(daemon, "openrouter_answer", side_effect=AssertionError("must not call LLM")):
            reply = daemon.answer_question({"OPENROUTER_API_KEY": "sk-test"}, "session overview")
        self.assertIn("🪟 Window:", reply)
        self.assertIn("✅ Last ping: success", reply)
        self.assertIn("📊 Session: 16% used", reply)
        self.assertIn("📅 Weekly: 4% used", reply)

    def test_slash_command_routes_to_status(self):
        now = int(datetime.datetime(2026, 8, 25, 9, 32, 0).timestamp())
        state = {"window_start": 0, "window_label": "07:02", "status": "success"}
        with patch.object(daemon, "load_state", return_value=state), \
                patch("time.time", return_value=now), \
                patch.object(daemon, "openrouter_answer", side_effect=AssertionError("must not call LLM")):
            reply = daemon.answer_question({"OPENROUTER_API_KEY": "sk-test"}, "/status")
        self.assertIn("🪟 Window:", reply)

    def test_slash_next_skips_usage_lookup(self):
        now = int(datetime.datetime(2026, 7, 13, 20, 7, 0).timestamp())
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), patch("time.time", return_value=now):
            reply = daemon.answer_question({}, "/next")
        self.assertEqual(reply, "Next session window starts at 22:02 (in 1h 55m).")


class TestFallbackPaths(NoRealUsageLookup):
    def test_unrecognized_question_without_api_key(self):
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE):
            reply = daemon.answer_question({}, "tell me a joke")
        self.assertEqual(reply, "I don't recognize that question and no OPENROUTER_API_KEY is configured.")

    def test_openrouter_network_error_returns_friendly_message(self):
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")), \
                patch.object(daemon, "log"):
            reply = daemon.openrouter_answer("sk-test", "openai/gpt-oss-20b", EMPTY_STATE, "hi")
        self.assertEqual(reply, "Sorry, I couldn't reach the answering service right now.")

    def test_openrouter_response_without_message_returns_friendly_message(self):
        import io

        fake = io.BytesIO(b'{"choices": []}')
        fake.__enter__ = lambda s: s
        fake.__exit__ = lambda s, *a: False
        with patch("urllib.request.urlopen", return_value=fake), patch.object(daemon, "log"):
            reply = daemon.openrouter_answer("sk-test", "openai/gpt-oss-20b", EMPTY_STATE, "hi")
        self.assertEqual(reply, "Sorry, I couldn't reach the answering service right now.")

    def test_openrouter_prompt_includes_live_usage(self):
        import io

        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode())
            fake = io.BytesIO(b'{"choices": [{"message": {"content": "ok"}}]}')
            fake.__enter__ = lambda s: s
            fake.__exit__ = lambda s, *a: False
            return fake

        usage = {
            "session": {"pct": 32.0, "resets_at": int(datetime.datetime(2026, 7, 16, 19, 10, 0).timestamp())},
            "weekly": {"pct": 95.0, "resets_at": int(datetime.datetime(2026, 7, 18, 18, 0, 0).timestamp())},
        }
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            reply = daemon.openrouter_answer(
                "sk-test", "openai/gpt-oss-20b", EMPTY_STATE, "am I close to my cap?", usage=usage
            )

        self.assertEqual(reply, "ok")
        system_prompt = captured["body"]["messages"][0]["content"]
        self.assertIn(
            "Live usage: session 32% used, resets 19:10; weekly 95% used, resets Sat 18:00. ",
            system_prompt,
        )

    def test_get_updates_network_failure_sleeps_before_retry(self):
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")), \
                patch.object(daemon, "log") as log_mock, \
                patch("time.sleep") as sleep_mock:
            result, status, error_message = daemon.get_updates("token", None)

        self.assertEqual(result, [])
        self.assertEqual(status, "error")
        self.assertEqual(error_message, "<urlopen error boom>")
        sleep_mock.assert_called_once_with(5)
        log_mock.assert_called_once()

    def test_get_updates_read_timeout_is_transient_and_silent(self):
        # The macOS DarkWake case: every wake freezes the long-poll socket and
        # surfaces as a read timeout. It's not an outage, so don't log it — one
        # line per wake is pure noise (~1-3/hour). See counts_toward_outage.
        with patch("urllib.request.urlopen", side_effect=TimeoutError("The read operation timed out")), \
                patch.object(daemon, "log") as log_mock, \
                patch("time.sleep"):
            result, status, error_message = daemon.get_updates("token", None)

        self.assertEqual(result, [])
        self.assertEqual(status, "transient")
        self.assertEqual(error_message, "The read operation timed out")
        log_mock.assert_not_called()

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
        now = int(datetime.datetime(2026, 7, 15, 20, 7, 0).timestamp())
        with patch.object(daemon, "get_usage", return_value=None), \
             patch.object(daemon, "load_state", return_value=EMPTY_STATE):
            got_usage, window_start = daemon.fetch_usage_and_window(now)
        self.assertIsNone(got_usage)
        self.assertEqual(window_start, int(datetime.datetime(2026, 7, 15, 17, 2, 0).timestamp()))

    def test_falls_back_to_schedule_when_usage_raises(self):
        now = int(datetime.datetime(2026, 7, 15, 20, 7, 0).timestamp())
        with patch.object(daemon, "get_usage", side_effect=OSError("boom")), \
             patch.object(daemon, "load_state", return_value=EMPTY_STATE), \
             patch.object(daemon, "log"):
            got_usage, window_start = daemon.fetch_usage_and_window(now)
        self.assertIsNone(got_usage)
        self.assertEqual(window_start, int(datetime.datetime(2026, 7, 15, 17, 2, 0).timestamp()))

    def test_weekly_only_usage_keeps_schedule_window(self):
        # No session entry -> window start still comes from the schedule path.
        now = int(datetime.datetime(2026, 7, 15, 20, 7, 0).timestamp())
        usage = {"session": None, "weekly": {"pct": 41.0, "resets_at": now + 86400}}
        with patch.object(daemon, "get_usage", return_value=usage), \
             patch.object(daemon, "load_state", return_value=EMPTY_STATE):
            got_usage, window_start = daemon.fetch_usage_and_window(now)
        self.assertEqual(got_usage, usage)
        self.assertEqual(window_start, int(datetime.datetime(2026, 7, 15, 17, 2, 0).timestamp()))

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


class TestNextTriggerFollowsTheInstalledSchedule(NoRealUsageLookup):
    """/next must report what launchd will really fire, not the constant.

    TARGETS is only the default. The times actually installed differ whenever
    a backup is pending (an arbitrary HH:MM at window end + buffer) or the
    plist template has been edited and ./install.sh re-run. Answering from the
    constant reported 22:02 while a backup was about to fire at 19:12.
    """

    def ask(self, question, when):
        with patch.object(daemon, "load_state", return_value=EMPTY_STATE), \
             patch("time.time", return_value=int(when.timestamp())):
            return daemon.answer_question({}, question)

    def test_pending_backup_is_the_next_trigger(self):
        self.write_agent("com.claude-session-ping.plist",
                         "07:02", "12:02", "17:02", "22:02")
        self.write_agent("com.claude-session-ping.backup-1912.plist", "19:12")
        reply = self.ask("/next", datetime.datetime(2026, 7, 13, 18, 30, 0))
        self.assertEqual(reply, "Next ping is a one-off backup at 19:12 (in 42m).")

    def test_regular_target_is_not_called_a_backup(self):
        self.write_agent("com.claude-session-ping.plist",
                         "07:02", "12:02", "17:02", "22:02")
        self.write_agent("com.claude-session-ping.backup-1912.plist", "19:12")
        # Past the backup, so the next trigger is the ordinary 22:02 target.
        reply = self.ask("/next", datetime.datetime(2026, 7, 13, 19, 30, 0))
        self.assertEqual(reply, "Next session window starts at 22:02 (in 2h 32m).")

    def test_edited_target_times_are_followed(self):
        # The schedule tweaked to hourly-ish targets: /next must track it
        # rather than answering from the 07/12/17/22 constant.
        self.write_agent("com.claude-session-ping.plist",
                         "06:30", "11:30", "16:30", "21:30")
        reply = self.ask("/next", datetime.datetime(2026, 7, 13, 12, 0, 0))
        self.assertEqual(reply, "Next session window starts at 16:30 (in 4h 30m).")

    def test_next_next_skips_over_the_backup(self):
        self.write_agent("com.claude-session-ping.plist",
                         "07:02", "12:02", "17:02", "22:02")
        self.write_agent("com.claude-session-ping.backup-1912.plist", "19:12")
        reply = self.ask("and the one after that?",
                         datetime.datetime(2026, 7, 13, 18, 30, 0))
        self.assertEqual(reply, "The ping after next is at 22:02 (in 3h 32m).")

    def test_status_reply_uses_the_installed_schedule(self):
        self.write_agent("com.claude-session-ping.plist",
                         "07:02", "12:02", "17:02", "22:02")
        self.write_agent("com.claude-session-ping.backup-1912.plist", "19:12")
        reply = self.ask("/status", datetime.datetime(2026, 7, 13, 18, 30, 0))
        self.assertIn("⏭️ Next start: 19:12", reply)
        self.assertIn("⏭️ Then: 22:02", reply)

    def test_the_daemons_own_agent_is_not_a_ping_trigger(self):
        # The Q&A bot's plist lives in the same directory and shares the
        # label prefix, but it is a long-poll daemon, not a scheduled ping.
        self.write_agent("com.claude-session-ping.plist", "07:02", "12:02")
        self.write_agent("com.claude-session-ping.telegram-bot.plist", "03:00")
        reply = self.ask("/next", datetime.datetime(2026, 7, 13, 1, 0, 0))
        self.assertEqual(reply, "Next session window starts at 07:02 (in 6h 2m).")

    def test_schedule_is_reread_for_every_question(self):
        # The daemon outlives the backups it reports on: ping runs create and
        # reap them while this process stays up, so a cached schedule would
        # go stale. (The 2026-08-25 lesson, in the other direction: a
        # long-running daemon holding old data looks like a broken feature.)
        self.write_agent("com.claude-session-ping.plist", "07:02", "22:02")
        at = datetime.datetime(2026, 7, 13, 18, 30, 0)
        self.assertEqual(self.ask("/next", at),
                         "Next session window starts at 22:02 (in 3h 32m).")

        backup = self.write_agent("com.claude-session-ping.backup-1912.plist", "19:12")
        self.assertEqual(self.ask("/next", at),
                         "Next ping is a one-off backup at 19:12 (in 42m).")

        backup.unlink()
        self.assertEqual(self.ask("/next", at),
                         "Next session window starts at 22:02 (in 3h 32m).")

    def test_unreadable_agent_dir_falls_back_to_the_documented_targets(self):
        # Same degradation rule as a failed usage lookup: fall back to the
        # schedule, never blank the answer.
        with patch.object(daemon, "AGENT_DIR", Path("/nonexistent/LaunchAgents")):
            reply = self.ask("/next", datetime.datetime(2026, 7, 13, 20, 7, 0))
        self.assertEqual(reply, "Next session window starts at 22:02 (in 1h 55m).")

    def test_corrupt_plist_is_skipped_not_fatal(self):
        self.write_agent("com.claude-session-ping.plist", "07:02", "22:02")
        (Path(self.agent_dir.name) / "com.claude-session-ping.backup-1912.plist").write_text(
            "this is not a plist"
        )
        reply = self.ask("/next", datetime.datetime(2026, 7, 13, 18, 30, 0))
        self.assertEqual(reply, "Next session window starts at 22:02 (in 3h 32m).")
