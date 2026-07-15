import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from usage_lib import derive_window_start, parse_usage_output

# Captured verbatim from `claude -p "/usage" --output-format json` on 2026-07-15.
REAL_SAMPLE = (
    "You are currently using your subscription to power your Claude Code usage\n\n"
    "Current session: 0% used · resets Jul 15 at 7:09pm (America/Guayaquil)\n"
    "Current week (all models): 91% used · resets Jul 18 at 11:59pm (America/Guayaquil)\n"
    "Current week (Fable): 95% used · resets Jul 18 at 11:59pm (America/Guayaquil)\n\n"
    "What's contributing to your limits usage?\n"
)


def ts(y, mo, d, h, mi):
    return int(datetime.datetime(y, mo, d, h, mi).timestamp())


class TestParseUsageOutput(unittest.TestCase):
    def test_parses_real_sample_session(self):
        now = ts(2026, 7, 15, 14, 15)
        result = parse_usage_output(REAL_SAMPLE, now)
        self.assertEqual(result["session"]["pct"], 0.0)
        self.assertEqual(result["session"]["resets_at"], ts(2026, 7, 15, 19, 9))

    def test_parses_weekly_max_across_lines(self):
        now = ts(2026, 7, 15, 14, 15)
        result = parse_usage_output(REAL_SAMPLE, now)
        self.assertEqual(result["weekly"]["pct"], 95.0)
        self.assertEqual(result["weekly"]["resets_at"], ts(2026, 7, 18, 23, 59))

    def test_parses_hour_only_time_variant(self):
        now = ts(2026, 7, 15, 14, 15)
        text = "Current session: 13% used · resets Jul 16 at 12am (America/Guayaquil)\n"
        result = parse_usage_output(text, now)
        self.assertEqual(result["session"]["resets_at"], ts(2026, 7, 16, 0, 0))

    def test_parses_noon_and_midnight_correctly(self):
        now = ts(2026, 7, 15, 10, 0)
        noon = parse_usage_output("Current session: 5% used · resets Jul 15 at 12pm (TZ)\n", now)
        self.assertEqual(noon["session"]["resets_at"], ts(2026, 7, 15, 12, 0))

    def test_rolls_over_year_when_reset_month_is_behind(self):
        now = ts(2026, 12, 31, 23, 0)
        result = parse_usage_output("Current session: 5% used · resets Jan 1 at 4am (TZ)\n", now)
        self.assertEqual(result["session"]["resets_at"], ts(2027, 1, 1, 4, 0))

    def test_absent_session_line_yields_none_session(self):
        now = ts(2026, 7, 15, 14, 15)
        text = "Current week (all models): 91% used · resets Jul 18 at 11:59pm (TZ)\n"
        result = parse_usage_output(text, now)
        self.assertIsNone(result["session"])
        self.assertEqual(result["weekly"]["pct"], 91.0)

    def test_unparseable_text_returns_none(self):
        now = ts(2026, 7, 15, 14, 15)
        self.assertIsNone(parse_usage_output("Login required. Run /login\n", now))

    def test_empty_text_returns_none(self):
        self.assertIsNone(parse_usage_output("", ts(2026, 7, 15, 14, 15)))


class TestDeriveWindowStart(unittest.TestCase):
    def test_window_start_is_five_hours_before_reset(self):
        self.assertEqual(
            derive_window_start(ts(2026, 7, 15, 19, 9)),
            ts(2026, 7, 15, 14, 9),
        )


if __name__ == "__main__":
    unittest.main()
