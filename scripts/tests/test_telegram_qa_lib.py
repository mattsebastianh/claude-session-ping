import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from telegram_qa_lib import (
    WINDOW_SECONDS,
    current_window_start,
    extract_output_text,
    format_day_time,
    format_time,
    format_usage_reply,
    humanize_delta,
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

    def test_clamped_at_0_when_start_in_future(self):
        now = 1_000_000
        self.assertEqual(usage_percent(now + 600, now), 0.0)


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


class TestCurrentWindowStart(unittest.TestCase):
    def test_inside_evening_window(self):
        now = int(datetime.datetime(2026, 7, 13, 22, 7, 0).timestamp())
        start = current_window_start(now)
        self.assertEqual(format_time(start), "19:00")

    def test_exactly_at_window_open(self):
        now = int(datetime.datetime(2026, 7, 13, 9, 0, 0).timestamp())
        start = current_window_start(now)
        self.assertEqual(format_time(start), "09:00")

    def test_in_gap_between_windows(self):
        # Windows are back-to-back from 04:00 to midnight; the only gap
        # is 00:00-04:00 (the 19:00 window ends at midnight).
        now = int(datetime.datetime(2026, 7, 13, 2, 30, 0).timestamp())
        self.assertEqual(current_window_start(now), 0)

    def test_just_after_midnight_window_closed(self):
        now = int(datetime.datetime(2026, 7, 13, 0, 30, 0).timestamp())
        self.assertEqual(current_window_start(now), 0)


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


class TestExtractOutputText(unittest.TestCase):
    def test_skips_reasoning_item(self):
        # gpt-5 responses put a reasoning item before the message item.
        result = {
            "output": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": " hi there "}]},
            ]
        }
        self.assertEqual(extract_output_text(result), "hi there")

    def test_message_first_still_works(self):
        result = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}
        self.assertEqual(extract_output_text(result), "ok")

    def test_no_message_returns_none(self):
        self.assertIsNone(extract_output_text({"output": [{"type": "reasoning"}]}))
        self.assertIsNone(extract_output_text({}))


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
        env = parse_env_text("export OPENAI_API_KEY=sk-test")
        self.assertEqual(env["OPENAI_API_KEY"], "sk-test")

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
