import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import claude_usage


def ts(y, mo, d, h, mi):
    return int(datetime.datetime(y, mo, d, h, mi).timestamp())


RESULT_JSON = '{"type":"result","result":"Current session: 1% used"}'
# An MCP server writes this to stdout — not stderr — after the JSON result.
MCP_NOISE = "Client.listTools() called but server does not advertise tools capability - returning empty list"


class TestFetchUsageText(unittest.TestCase):
    def _run(self, stdout, returncode=0):
        completed = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")
        with patch("subprocess.run", return_value=completed):
            return claude_usage.fetch_usage_text()

    def test_returns_result_string_on_success(self):
        self.assertEqual(self._run(RESULT_JSON), ("Current session: 1% used", ""))

    def test_returns_none_on_nonzero_exit(self):
        self.assertEqual(self._run("", returncode=1), (None, "exit_1"))

    def test_returns_none_on_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 30)):
            self.assertEqual(claude_usage.fetch_usage_text(), (None, "timeout"))

    def test_returns_none_when_claude_missing(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            self.assertEqual(claude_usage.fetch_usage_text(), (None, "not_found"))

    def test_returns_none_on_bad_json(self):
        self.assertEqual(self._run("not json"), (None, "bad_json"))

    def test_returns_none_when_result_is_not_a_string(self):
        self.assertEqual(self._run('{"type":"result","result":null}'), (None, "no_result"))

    def test_ignores_mcp_noise_printed_after_the_json(self):
        # 2026-08-14: this trailing stdout line made json.loads raise
        # "Extra data", so 77 of 136 lookups silently degraded to the
        # schedule — and a 07:02 ping absorbed by an existing window was
        # announced as having opened one.
        self.assertEqual(
            self._run(f"{RESULT_JSON}\n{MCP_NOISE}\n"),
            ("Current session: 1% used", ""),
        )

    def test_ignores_noise_printed_before_the_json(self):
        self.assertEqual(
            self._run(f"{MCP_NOISE}\n{RESULT_JSON}\n"),
            ("Current session: 1% used", ""),
        )

    def test_reports_bad_json_when_no_line_is_a_result(self):
        self.assertEqual(self._run(f"{MCP_NOISE}\nstill not json\n"), (None, "bad_json"))


class TestGetUsageWithReason(unittest.TestCase):
    def test_reports_unparsed_when_prose_does_not_match(self):
        with patch.object(claude_usage, "fetch_usage_text", return_value=("gibberish", "")):
            usage, reason = claude_usage.get_usage_with_reason(ts(2026, 8, 14, 12, 2))
        self.assertIsNone(usage)
        self.assertEqual(reason, "unparsed")

    def test_passes_the_fetch_reason_through(self):
        with patch.object(claude_usage, "fetch_usage_text", return_value=(None, "timeout")):
            self.assertEqual(claude_usage.get_usage_with_reason(0), (None, "timeout"))

    def test_get_usage_still_returns_only_the_dict(self):
        # The Telegram daemon calls this one.
        text = "Current session: 1% used · resets Aug 14 at 12:59pm (America/Guayaquil)"
        with patch.object(claude_usage, "fetch_usage_text", return_value=(text, "")):
            usage = claude_usage.get_usage(ts(2026, 8, 14, 12, 2))
        self.assertEqual(usage["session"]["resets_at"], ts(2026, 8, 14, 12, 59))


class TestShellLines(unittest.TestCase):
    def test_reports_not_ok_when_usage_is_none(self):
        self.assertEqual(claude_usage.shell_lines(None, ts(2026, 7, 15, 14, 15)), ["USAGE_OK=0"])

    def test_reports_not_ok_when_session_absent(self):
        usage = {"session": None, "weekly": {"pct": 50.0, "resets_at": 0}}
        self.assertEqual(claude_usage.shell_lines(usage, ts(2026, 7, 15, 14, 15)), ["USAGE_OK=0"])

    def test_emits_the_failure_reason_for_the_log(self):
        # Without this the shell logs one undifferentiated "usage lookup
        # unavailable" for timeouts, crashes and parse failures alike.
        lines = claude_usage.shell_lines(None, ts(2026, 8, 14, 12, 2), reason="bad_json")
        self.assertEqual(lines, ["USAGE_OK=0", "USAGE_ERROR=bad_json"])

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

    def test_marks_window_new_when_start_is_anchored_before_a_late_ping(self):
        # Regression, 2026-07-18 04:04: reset reported 9:00am -> start 04:00,
        # 290s before the lookup — beyond the old 180s tolerance despite the
        # ping having opened the window (previous window ended 00:31).
        now = ts(2026, 7, 18, 4, 4) + 50
        usage = {"session": {"pct": 0.0, "resets_at": ts(2026, 7, 18, 9, 0)}, "weekly": None}
        lines = claude_usage.shell_lines(usage, now, prev_resets_at=ts(2026, 7, 18, 0, 31))
        self.assertIn("WINDOW_IS_NEW=1", lines)

    def test_prev_reset_marks_known_window_preexisting(self):
        now = ts(2026, 7, 17, 14, 35)
        usage = {"session": {"pct": 5.0, "resets_at": ts(2026, 7, 17, 19, 10)}, "weekly": None}
        lines = claude_usage.shell_lines(usage, now, prev_resets_at=ts(2026, 7, 17, 14, 30))
        self.assertIn("WINDOW_IS_NEW=0", lines)


class TestReadPrevResetsAt(unittest.TestCase):
    def _write_state(self, payload):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.addCleanup(os.unlink, tmp.name)
        tmp.write(payload)
        tmp.close()
        return tmp.name

    def test_reads_resets_at_from_state(self):
        path = self._write_state(json.dumps({"window_start": 1, "resets_at": 1784383200}))
        self.assertEqual(claude_usage.read_prev_resets_at(path), 1784383200)

    def test_missing_file_returns_none(self):
        missing = Path(tempfile.mkdtemp()) / "absent.json"
        self.assertIsNone(claude_usage.read_prev_resets_at(str(missing)))

    def test_state_without_resets_at_returns_none(self):
        # Older state files (and fallback-path writes) lack the field.
        path = self._write_state(json.dumps({"window_start": 1, "status": "success"}))
        self.assertIsNone(claude_usage.read_prev_resets_at(path))

    def test_malformed_state_returns_none(self):
        path = self._write_state("not json")
        self.assertIsNone(claude_usage.read_prev_resets_at(path))

    def test_non_numeric_resets_at_returns_none(self):
        path = self._write_state(json.dumps({"resets_at": "soon"}))
        self.assertIsNone(claude_usage.read_prev_resets_at(path))

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
