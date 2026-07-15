import datetime
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import claude_usage


def ts(y, mo, d, h, mi):
    return int(datetime.datetime(y, mo, d, h, mi).timestamp())


class TestFetchUsageText(unittest.TestCase):
    def test_returns_result_string_on_success(self):
        payload = '{"type":"result","result":"Current session: 1% used"}'
        completed = subprocess.CompletedProcess([], 0, stdout=payload, stderr="")
        with patch("subprocess.run", return_value=completed):
            self.assertEqual(claude_usage.fetch_usage_text(), "Current session: 1% used")

    def test_returns_none_on_nonzero_exit(self):
        completed = subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
        with patch("subprocess.run", return_value=completed):
            self.assertIsNone(claude_usage.fetch_usage_text())

    def test_returns_none_on_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 30)):
            self.assertIsNone(claude_usage.fetch_usage_text())

    def test_returns_none_when_claude_missing(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            self.assertIsNone(claude_usage.fetch_usage_text())

    def test_returns_none_on_bad_json(self):
        completed = subprocess.CompletedProcess([], 0, stdout="not json", stderr="")
        with patch("subprocess.run", return_value=completed):
            self.assertIsNone(claude_usage.fetch_usage_text())


class TestShellLines(unittest.TestCase):
    def test_reports_not_ok_when_usage_is_none(self):
        self.assertEqual(claude_usage.shell_lines(None, ts(2026, 7, 15, 14, 15)), ["USAGE_OK=0"])

    def test_reports_not_ok_when_session_absent(self):
        usage = {"session": None, "weekly": {"pct": 50.0, "resets_at": 0}}
        self.assertEqual(claude_usage.shell_lines(usage, ts(2026, 7, 15, 14, 15)), ["USAGE_OK=0"])

    def test_marks_window_new_when_start_is_within_tolerance(self):
        now = ts(2026, 7, 15, 14, 10)
        usage = {"session": {"pct": 0.0, "resets_at": ts(2026, 7, 15, 19, 9)}, "weekly": None}
        lines = claude_usage.shell_lines(usage, now)
        self.assertIn("USAGE_OK=1", lines)
        self.assertIn("WINDOW_IS_NEW=1", lines)
        self.assertIn(f"WINDOW_START={ts(2026, 7, 15, 14, 9)}", lines)

    def test_marks_window_preexisting_when_start_is_old(self):
        now = ts(2026, 7, 15, 14, 0)
        usage = {"session": {"pct": 60.0, "resets_at": ts(2026, 7, 15, 14, 9)}, "weekly": None}
        lines = claude_usage.shell_lines(usage, now)
        self.assertIn("WINDOW_IS_NEW=0", lines)

    def test_weekly_warn_set_above_threshold(self):
        now = ts(2026, 7, 15, 14, 10)
        usage = {
            "session": {"pct": 0.0, "resets_at": ts(2026, 7, 15, 19, 9)},
            "weekly": {"pct": 95.0, "resets_at": ts(2026, 7, 18, 23, 59)},
        }
        lines = claude_usage.shell_lines(usage, now)
        self.assertIn("WEEKLY_WARN=1", lines)
        self.assertIn("WEEKLY_PCT=95", lines)

    def test_weekly_warn_clear_below_threshold(self):
        now = ts(2026, 7, 15, 14, 10)
        usage = {
            "session": {"pct": 0.0, "resets_at": ts(2026, 7, 15, 19, 9)},
            "weekly": {"pct": 10.0, "resets_at": ts(2026, 7, 18, 23, 59)},
        }
        lines = claude_usage.shell_lines(usage, now)
        self.assertIn("WEEKLY_WARN=0", lines)


if __name__ == "__main__":
    unittest.main()
