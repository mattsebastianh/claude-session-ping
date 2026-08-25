import datetime
import os
import socket
import ssl
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telegram_qa_lib import (
    BOT_COMMANDS,
    WINDOW_SECONDS,
    command_intent,
    counts_toward_outage,
    current_window_start,
    extract_chat_completion_text,
    next_failure_count,
    format_day_time,
    format_status_reply,
    format_time,
    format_usage_reply,
    humanize_delta,
    match_intent,
    next_start_times,
    parse_env_text,
    usage_percent,
    usage_prompt_line,
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

    def test_clamped_at_0_when_start_in_future(self):
        now = 1_000_000
        self.assertEqual(usage_percent(now + 600, now), 0.0)


class TestWindowEnd(unittest.TestCase):
    def test_adds_five_hours(self):
        self.assertEqual(window_end(1_000_000), 1_000_000 + WINDOW_SECONDS)


class TestNextStartTimes(unittest.TestCase):
    def test_returns_next_two_in_order(self):
        now = int(datetime.datetime(2026, 7, 13, 11, 0, 0).timestamp())
        starts = next_start_times(now)
        self.assertEqual(len(starts), 2)
        self.assertEqual(format_time(starts[0]), "12:02")
        self.assertEqual(format_time(starts[1]), "17:02")

    def test_rolls_over_to_next_day(self):
        now = int(datetime.datetime(2026, 7, 13, 23, 0, 0).timestamp())
        starts = next_start_times(now)
        self.assertEqual(format_time(starts[0]), "07:02")
        self.assertEqual(format_time(starts[1]), "12:02")


class TestCurrentWindowStart(unittest.TestCase):
    def test_inside_evening_window(self):
        now = int(datetime.datetime(2026, 7, 13, 23, 7, 0).timestamp())
        start = current_window_start(now)
        self.assertEqual(format_time(start), "22:02")

    def test_exactly_at_window_open(self):
        now = int(datetime.datetime(2026, 7, 13, 12, 2, 0).timestamp())
        start = current_window_start(now)
        self.assertEqual(format_time(start), "12:02")

    def test_in_gap_between_windows(self):
        # Windows are back-to-back from 07:02 to 03:02; the only gap
        # is 03:02-07:02 (the 22:02 window ends just after 03:00).
        now = int(datetime.datetime(2026, 7, 13, 5, 30, 0).timestamp())
        self.assertEqual(current_window_start(now), 0)

    def test_just_after_midnight_still_in_evening_window(self):
        # The 22:02 window spans midnight, so yesterday's target owns 00:30.
        now = int(datetime.datetime(2026, 7, 13, 0, 30, 0).timestamp())
        start = current_window_start(now)
        self.assertEqual(format_time(start), "22:02")


class TestHumanizeDelta(unittest.TestCase):
    def test_hours_and_minutes(self):
        self.assertEqual(humanize_delta(5 * 3600 + 53 * 60), "5h 53m")

    def test_minutes_only(self):
        self.assertEqual(humanize_delta(42 * 60), "42m")

    def test_less_than_a_minute(self):
        self.assertEqual(humanize_delta(30), "under a minute")

    def test_exact_hours(self):
        self.assertEqual(humanize_delta(2 * 3600), "2h 0m")

    def test_negative_treated_as_under_a_minute(self):
        # Clock skew between state file and daemon shouldn't produce "-1m".
        self.assertEqual(humanize_delta(-500), "under a minute")

    def test_days_and_hours(self):
        self.assertEqual(humanize_delta(2 * 86400 + 4 * 3600 + 10 * 60), "2d 4h")

    def test_exact_one_day(self):
        self.assertEqual(humanize_delta(86400), "1d 0h")


class TestFormatDayTime(unittest.TestCase):
    def test_renders_weekday_and_time(self):
        # 2026-07-16 is a Thursday.
        epoch = int(datetime.datetime(2026, 7, 16, 18, 0, 0).timestamp())
        self.assertEqual(format_day_time(epoch), "Thu 18:00")


class TestCommandIntent(unittest.TestCase):
    def test_maps_each_command(self):
        self.assertEqual(command_intent("/status"), "status")
        self.assertEqual(command_intent("/usage"), "usage")
        self.assertEqual(command_intent("/window"), "window_open")
        self.assertEqual(command_intent("/ends"), "window_end")
        self.assertEqual(command_intent("/next"), "next_start")

    def test_accepts_botname_suffix(self):
        # Telegram appends @BotName when a command is used in a group.
        self.assertEqual(command_intent("/status@ClaudeWindowBot"), "status")

    def test_ignores_trailing_arguments(self):
        self.assertEqual(command_intent("/usage please"), "usage")

    def test_plain_text_is_not_a_command(self):
        self.assertIsNone(command_intent("status"))
        self.assertIsNone(command_intent("what's my usage"))

    def test_unknown_command_returns_none(self):
        self.assertIsNone(command_intent("/frobnicate"))

    def test_every_advertised_command_maps_to_an_intent(self):
        # BOT_COMMANDS drives the Telegram menu; a typo there would advertise
        # a command that falls through to the LLM.
        for name, _desc in BOT_COMMANDS:
            self.assertIsNotNone(command_intent(f"/{name}"), f"/{name} has no intent")


class TestFormatStatusReply(unittest.TestCase):
    def setUp(self):
        self.now = int(datetime.datetime(2026, 8, 25, 9, 32, 0).timestamp())
        self.window_start = int(datetime.datetime(2026, 8, 25, 7, 2, 0).timestamp())
        self.usage = {
            "session": {"pct": 16.0, "resets_at": int(datetime.datetime(2026, 8, 25, 12, 2, 0).timestamp())},
            # 2026-08-30 is a Sunday.
            "weekly": {"pct": 4.0, "resets_at": int(datetime.datetime(2026, 8, 30, 0, 0, 0).timestamp())},
        }

    def test_full_status_has_one_fact_per_line(self):
        reply = format_status_reply(self.usage, self.window_start, "success", self.now)
        self.assertEqual(
            reply,
            "🪟 Window: 07:02–12:02 (50% elapsed)\n"
            "✅ Last ping: success\n"
            "⏭️ Next start: 12:02\n"
            "⏭️ Then: 17:02\n"
            "📊 Session: 16% used — resets 12:02 (2h 30m left)\n"
            "📅 Weekly: 4% used — resets Sun 00:00 (4d 14h left)",
        )

    def test_failed_ping_is_flagged(self):
        reply = format_status_reply(self.usage, self.window_start, "failure", self.now)
        self.assertIn("⚠️ Last ping: failure", reply)

    def test_no_window_active(self):
        reply = format_status_reply(self.usage, 0, "success", self.now)
        self.assertIn("🪟 Window: none active", reply)

    def test_usage_unavailable_falls_back_to_estimate(self):
        reply = format_status_reply(None, self.window_start, "success", self.now)
        self.assertIn("🪟 Window: 07:02–12:02 (50% elapsed)", reply)
        self.assertIn("⚠️ Live usage unavailable — window figures are schedule estimates", reply)
        self.assertNotIn("📊 Session:", reply)

    def test_omits_weekly_when_absent(self):
        usage = {"session": self.usage["session"], "weekly": None}
        reply = format_status_reply(usage, self.window_start, "success", self.now)
        self.assertIn("📊 Session:", reply)
        self.assertNotIn("📅 Weekly:", reply)


class TestExtractChatCompletionText(unittest.TestCase):
    def test_extracts_message_content(self):
        result = {"choices": [{"message": {"content": " hi there "}}]}
        self.assertEqual(extract_chat_completion_text(result), "hi there")

    def test_no_choices_returns_none(self):
        self.assertIsNone(extract_chat_completion_text({"choices": []}))
        self.assertIsNone(extract_chat_completion_text({}))

    def test_empty_content_returns_none(self):
        self.assertIsNone(extract_chat_completion_text({"choices": [{"message": {"content": ""}}]}))


class TestMatchIntent(unittest.TestCase):
    def test_usage(self):
        self.assertEqual(match_intent("what's my usage %?"), "usage")

    def test_session_usage_phrasing(self):
        self.assertEqual(match_intent("what's my session usage"), "usage")

    def test_window_opened_matches_window_open(self):
        self.assertEqual(match_intent("when this current window opened?"), "window_open")

    def test_when_did_it_open(self):
        self.assertEqual(match_intent("when did the session open"), "window_open")

    def test_opening_hours_not_window_open(self):
        # "open" needs word boundaries so e.g. "reopening" doesn't match.
        self.assertEqual(match_intent("tell me about reopening plans"), "none")

    def test_reset_matches_next_start(self):
        self.assertEqual(match_intent("when is the next reset"), "next_start")

    def test_session_reset_matches_next_start(self):
        self.assertEqual(match_intent("when is the session reset"), "next_start")

    def test_window_end(self):
        self.assertEqual(match_intent("when does this window end"), "window_end")

    def test_next_start(self):
        self.assertEqual(match_intent("what's the next session start time?"), "next_start")

    def test_next_next_start(self):
        self.assertEqual(match_intent("what about the next next one"), "next_next_start")

    def test_none(self):
        self.assertEqual(match_intent("tell me a joke"), "none")

    def test_session_overview_matches_status(self):
        self.assertEqual(match_intent("session overview"), "status")

    def test_session_info_matches_status(self):
        self.assertEqual(match_intent("give me current session info"), "status")

    def test_bare_status_matches_status(self):
        self.assertEqual(match_intent("status"), "status")

    def test_summary_matches_status(self):
        self.assertEqual(match_intent("summary please"), "status")

    def test_specific_facet_still_beats_status(self):
        # status is matched last, so naming one facet keeps the narrow answer.
        self.assertEqual(match_intent("quota status please"), "usage")
        self.assertEqual(match_intent("when does this window end"), "window_end")
        self.assertEqual(match_intent("what's my usage %?"), "usage")

    def test_weekend_does_not_match_window_end(self):
        self.assertEqual(match_intent("anything happening this weekend?"), "none")

    def test_recover_does_not_match_window_end(self):
        self.assertEqual(match_intent("did you recover the file"), "none")

    def test_friend_does_not_match_window_end(self):
        self.assertEqual(match_intent("I have a friend"), "none")

    def test_weekly_limit_matches_usage(self):
        self.assertEqual(match_intent("what's my weekly limit?"), "usage")

    def test_used_matches_usage(self):
        self.assertEqual(match_intent("have I used a lot today?"), "usage")

    def test_quota_matches_usage(self):
        self.assertEqual(match_intent("quota status please"), "usage")

    def test_remaining_matches_usage(self):
        self.assertEqual(match_intent("whats remaining this week"), "usage")

    def test_caused_does_not_match_usage(self):
        # "used" must be word-boundary matched.
        self.assertEqual(match_intent("what caused the failure"), "none")

    def test_unlimited_does_not_match_usage(self):
        # "limit" must be word-boundary matched.
        self.assertEqual(match_intent("is the plan unlimited"), "none")

    def test_precedence_next_window_end_prefers_next_start(self):
        # Ambiguous question hits both "next window" and "end"; intent
        # order deliberately resolves to next_start. Lock that in so a
        # keyword reshuffle doesn't silently change behavior.
        self.assertEqual(match_intent("when does the next window end"), "next_start")


class TestFormatUsageReply(unittest.TestCase):
    NOW = int(datetime.datetime(2026, 7, 16, 16, 30, 0).timestamp())  # Thu
    SESSION_RESET = int(datetime.datetime(2026, 7, 16, 19, 10, 0).timestamp())
    WEEKLY_RESET = int(datetime.datetime(2026, 7, 18, 18, 0, 0).timestamp())  # Sat

    def test_session_and_weekly(self):
        usage = {
            "session": {"pct": 32.0, "resets_at": self.SESSION_RESET},
            "weekly": {"pct": 95.0, "resets_at": self.WEEKLY_RESET},
        }
        self.assertEqual(
            format_usage_reply(usage, self.NOW),
            "📊 Session: 32% used — resets 19:10 (2h 40m left)\n"
            "📅 Weekly: 95% used — resets Sat 18:00 (2d 1h left)",
        )

    def test_session_only(self):
        usage = {"session": {"pct": 5.0, "resets_at": self.SESSION_RESET}, "weekly": None}
        self.assertEqual(
            format_usage_reply(usage, self.NOW),
            "📊 Session: 5% used — resets 19:10 (2h 40m left)",
        )

    def test_weekly_only(self):
        usage = {"session": None, "weekly": {"pct": 41.0, "resets_at": self.WEEKLY_RESET}}
        self.assertEqual(
            format_usage_reply(usage, self.NOW),
            "📅 Weekly: 41% used — resets Sat 18:00 (2d 1h left)",
        )


class TestUsagePromptLine(unittest.TestCase):
    def test_session_and_weekly(self):
        usage = {
            "session": {"pct": 32.0, "resets_at": int(datetime.datetime(2026, 7, 16, 19, 10, 0).timestamp())},
            "weekly": {"pct": 95.0, "resets_at": int(datetime.datetime(2026, 7, 18, 18, 0, 0).timestamp())},
        }
        self.assertEqual(
            usage_prompt_line(usage),
            "Live usage: session 32% used, resets 19:10; weekly 95% used, resets Sat 18:00. ",
        )

    def test_session_only(self):
        usage = {
            "session": {"pct": 5.0, "resets_at": int(datetime.datetime(2026, 7, 16, 19, 10, 0).timestamp())},
            "weekly": None,
        }
        self.assertEqual(usage_prompt_line(usage), "Live usage: session 5% used, resets 19:10. ")

    def test_unavailable_returns_empty(self):
        self.assertEqual(usage_prompt_line(None), "")
        self.assertEqual(usage_prompt_line({"session": None, "weekly": None}), "")


class TestCountsTowardOutage(unittest.TestCase):
    def test_read_timeout_is_not_an_outage(self):
        # Every macOS DarkWake kills the pending long poll; that is normal,
        # not an outage. Counting it sent 12 false alarms in one night.
        self.assertFalse(counts_toward_outage(TimeoutError("The read operation timed out")))

    def test_url_error_wrapping_a_timeout_is_not_an_outage(self):
        exc = urllib.error.URLError(TimeoutError("timed out"))
        self.assertFalse(counts_toward_outage(exc))

    def test_socket_timeout_is_not_an_outage(self):
        # What urllib actually raises on a read timeout. On Python 3.9 (the
        # interpreter launchd runs the daemon under) socket.timeout is NOT a
        # TimeoutError subclass — they were only unified in 3.10 — so an
        # isinstance(exc, TimeoutError) check silently misses every DarkWake
        # timeout on that runtime.
        self.assertFalse(counts_toward_outage(socket.timeout("The read operation timed out")))

    def test_url_error_wrapping_a_socket_timeout_is_not_an_outage(self):
        exc = urllib.error.URLError(socket.timeout("timed out"))
        self.assertFalse(counts_toward_outage(exc))

    def test_ssl_read_timeout_is_not_an_outage(self):
        # SSL-layer reads (post-handshake) surface a DarkWake timeout as
        # ssl.SSLError, not socket.timeout/TimeoutError, but it's the same
        # dead-socket-after-sleep case as the plain read timeout above.
        self.assertFalse(counts_toward_outage(ssl.SSLError("The read operation timed out")))

    def test_ssl_handshake_timeout_wrapped_in_url_error_is_not_an_outage(self):
        exc = urllib.error.URLError(ssl.SSLError("_ssl.c:1112: The handshake operation timed out"))
        self.assertFalse(counts_toward_outage(exc))

    def test_connection_reset_is_an_outage(self):
        self.assertTrue(counts_toward_outage(urllib.error.URLError("[Errno 54] Connection reset by peer")))

    def test_json_decode_error_is_an_outage(self):
        self.assertTrue(counts_toward_outage(ValueError("bad json")))


class TestNextFailureCount(unittest.TestCase):
    def test_ok_resets(self):
        self.assertEqual(next_failure_count(7, "ok"), 0)

    def test_error_increments(self):
        self.assertEqual(next_failure_count(2, "error"), 3)

    def test_transient_holds_steady(self):
        # A sleep timeout is neither progress nor an outage: hold the count
        # so a real outage after sleep still alerts at the right threshold.
        self.assertEqual(next_failure_count(2, "transient"), 2)

    def test_transient_streak_never_reaches_threshold(self):
        count = 0
        for _ in range(20):
            count = next_failure_count(count, "transient")
        self.assertEqual(count, 0)


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

    def test_export_prefix(self):
        # The env file is source'd by zsh, where `export KEY=...` is legal.
        env = parse_env_text("export OPENROUTER_API_KEY=sk-test")
        self.assertEqual(env["OPENROUTER_API_KEY"], "sk-test")

    def test_value_containing_equals(self):
        env = parse_env_text("KEY=abc==")
        self.assertEqual(env["KEY"], "abc==")

    def test_inline_comment_stripped_from_unquoted_value(self):
        # zsh would give KEY=val here; our parser must agree.
        env = parse_env_text("KEY=val # trailing comment")
        self.assertEqual(env["KEY"], "val")

    def test_hash_inside_quotes_preserved(self):
        env = parse_env_text("KEY='val # not a comment'")
        self.assertEqual(env["KEY"], "val # not a comment")


if __name__ == "__main__":
    unittest.main()
