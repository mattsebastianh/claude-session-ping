"""Integration tests for scripts/claude_session_ping.sh.

Drives the real zsh script with every side effect stubbed out: the ping
command, the usage lookup, the env file (so no Telegram creds are sourced
and no message is sent), the state file, and the clock.

These cover the scheduling guard, which is not exercised by the pure Python
tests but is where a missed keepalive costs a whole 5-hour window.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "claude_session_ping.sh"


class PingScriptCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_file = Path(self.tmp.name) / "state.json"
        self.log_file = Path(self.tmp.name) / "ping.log"

    def run_ping(self, mock_time, usage="USAGE_OK=0", grace=None, backup_label=None,
                 extra_env=None):
        """Run the script at `mock_time`; returns (exit_code, log_text).

        `usage` may contain multiple newline-separated KEY=VALUE lines; it is
        emitted via printf so load_usage sees each on its own line.
        """
        usage_cmd = "printf '%s\\n' " + " ".join(
            "'%s'" % line for line in usage.split("\n")
        )
        env = {
            "PATH": os.environ["PATH"],
            "HOME": os.environ["HOME"],
            "CLAUDE_SESSION_PING_MOCK_TIME": mock_time,
            "CLAUDE_SESSION_PING_COMMAND": "echo mock-ping",
            "CLAUDE_SESSION_PING_USAGE_CMD": usage_cmd,
            "CLAUDE_SESSION_PING_ENV_FILE": "/dev/null",
            "CLAUDE_SESSION_PING_STATE_FILE": str(self.state_file),
            "CLAUDE_SESSION_PING_LOG": str(self.log_file),
        }
        if grace is not None:
            env["CLAUDE_SESSION_PING_GRACE_MINUTES"] = str(grace)
        if backup_label is not None:
            env["CLAUDE_SESSION_PING_BACKUP_LABEL"] = backup_label
        if extra_env:
            env.update(extra_env)
        completed = subprocess.run(
            ["zsh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30
        )
        log = self.log_file.read_text() if self.log_file.exists() else ""
        return completed.returncode, log


class TestScheduleGuard(PingScriptCase):
    def test_pings_exactly_on_target(self):
        code, log = self.run_ping("09:02")
        self.assertEqual(code, 0)
        self.assertIn("sent successfully", log)

    def test_pings_when_launchd_fires_late_after_wake(self):
        # The real failure: Mac asleep at the target, DarkWake at 09:07:45,
        # launchd ran the missed job at 09:07:46 -> "skip (current time 0907)"
        # and the window never opened.
        code, log = self.run_ping("09:07")
        self.assertEqual(code, 0)
        self.assertIn("sent successfully", log)

    def test_skips_outside_the_grace_window(self):
        # 45 min late: the window is well underway; a ping here would open a
        # window at a time the schedule never intended.
        code, log = self.run_ping("09:45")
        self.assertEqual(code, 0)
        self.assertIn("skip", log)
        self.assertNotIn("sent successfully", log)

    def test_skips_when_no_target_is_near(self):
        code, log = self.run_ping("11:30")
        self.assertEqual(code, 0)
        self.assertIn("skip", log)

    def test_never_pings_before_a_target(self):
        # 08:59 must not open the 09:02 window early.
        code, log = self.run_ping("08:59")
        self.assertEqual(code, 0)
        self.assertIn("skip", log)
        self.assertNotIn("sent successfully", log)

    def test_grace_window_is_configurable(self):
        code, log = self.run_ping("09:45", grace=60)
        self.assertEqual(code, 0)
        self.assertIn("sent successfully", log)


class TestStateGuard(PingScriptCase):
    def test_second_run_in_same_grace_window_does_not_ping_twice(self):
        code, log = self.run_ping("09:02")
        self.assertIn("sent successfully", log)

        # launchd can fire a missed job again after another wake.
        code, log = self.run_ping("09:07")
        self.assertEqual(code, 0)
        self.assertIn("already pinged", log)

    def test_retries_a_window_whose_ping_failed(self):
        # A failed attempt must not block a later retry inside the grace window.
        self.state_file.write_text(
            '{"window_start": 0, "window_label": "09:02", "status": "failed", "updated_at": %d}'
            % int(__import__("time").time())
        )
        code, log = self.run_ping("09:07")
        self.assertIn("sent successfully", log)

    def test_stale_state_from_a_previous_day_does_not_block(self):
        # Same label, but yesterday's window: must still ping today.
        yesterday = int(__import__("time").time()) - 86400
        self.state_file.write_text(
            '{"window_start": 0, "window_label": "09:02", "status": "success", "updated_at": %d}'
            % yesterday
        )
        code, log = self.run_ping("09:07")
        self.assertIn("sent successfully", log)


class TestStateRecordsResets(PingScriptCase):
    def test_real_window_success_records_resets_at(self):
        # The next run's usage lookup compares the reported window start
        # against this recorded reset to tell a fresh window from the one it
        # already knew about.
        resets = 1784383200
        usage = (
            "USAGE_OK=1\n"
            "SESSION_PCT=0\n"
            f"SESSION_RESETS_AT={resets}\n"
            f"WINDOW_START={resets - 18000}\n"
            "WINDOW_IS_NEW=1"
        )
        self.run_ping("09:02", usage=usage)
        self.assertIn(f'"resets_at": {resets}', self.state_file.read_text())

    def test_fallback_success_omits_resets_at(self):
        # Usage lookup unavailable: the window end is only an estimate, so it
        # must not be recorded as a real reset.
        self.run_ping("09:02", usage="USAGE_OK=0")
        state = self.state_file.read_text()
        self.assertIn('"status": "success"', state)
        self.assertNotIn("resets_at", state)


class TestWindowLabel(PingScriptCase):
    def test_late_run_records_the_target_not_the_wake_time(self):
        # State must say the 09:02 window, not "09:07".
        self.run_ping("09:07")
        self.assertIn('"window_label": "09:02"', self.state_file.read_text())


class TestBackupMode(PingScriptCase):
    def test_backup_label_pings_off_target_and_bypasses_matching(self):
        # 14:37 matches no scheduled target; without backup mode this skips.
        code, log = self.run_ping("14:37", backup_label="14:37")
        self.assertEqual(code, 0)
        self.assertIn("sent successfully", log)
        self.assertIn('"window_label": "14:37"', self.state_file.read_text())

    def test_off_target_without_backup_label_still_skips(self):
        code, log = self.run_ping("14:37")
        self.assertEqual(code, 0)
        self.assertIn("skip", log)
        self.assertNotIn("sent successfully", log)

    def _backup_env(self, kill_on=None):
        """launchctl stub that appends its argv to a log; isolated backup dir.

        `kill_on`: SIGTERM the calling script when the stub's job argument
        matches — simulating launchctl unload/remove of the very job the
        running backup instance was started from.
        """
        stub = Path(self.tmp.name) / "launchctl_stub.sh"
        calls = Path(self.tmp.name) / "launchctl_calls.txt"
        kill_clause = (
            'if [[ "$2" == *%s* ]]; then kill $PPID; fi\n' % kill_on if kill_on else ""
        )
        stub.write_text(
            "#!/usr/bin/env zsh\nprint \"$@\" >> '%s'\n%s" % (calls, kill_clause)
        )
        stub.chmod(0o755)
        backup_dir = Path(self.tmp.name) / "agents"
        backup_dir.mkdir()
        return {
            "CLAUDE_SESSION_PING_LAUNCHCTL": str(stub),
            "CLAUDE_SESSION_PING_BACKUP_DIR": str(backup_dir),
        }, calls, backup_dir

    # Multiline usage: existing window, no new window, resets at a fixed epoch.
    def _no_new_usage(self, resets_at):
        return (
            "USAGE_OK=1\n"
            "SESSION_PCT=10\n"
            f"SESSION_RESETS_AT={resets_at}\n"
            f"WINDOW_START={resets_at - 18000}\n"
            "WINDOW_IS_NEW=0"
        )

    def _new_usage(self, start_at):
        return (
            "USAGE_OK=1\n"
            "SESSION_PCT=0\n"
            f"SESSION_RESETS_AT={start_at + 18000}\n"
            f"WINDOW_START={start_at}\n"
            "WINDOW_IS_NEW=1"
        )

    def test_no_new_window_schedules_a_backup_plist(self):
        env, calls, backup_dir = self._backup_env()
        # Deterministic window end at 14:30 today -> fire 14:32, inside cutoff.
        resets = int(__import__("datetime").datetime.now().replace(
            hour=14, minute=30, second=0, microsecond=0).timestamp())
        code, log = self.run_ping("14:02", usage=self._no_new_usage(resets),
                                  extra_env=env)
        self.assertEqual(code, 0)
        plists = list(backup_dir.glob("com.claude-session-ping.backup-*.plist"))
        self.assertEqual(len(plists), 1)
        self.assertIn("backup-1432", plists[0].name)
        self.assertIn("load", calls.read_text())

    def test_new_window_clears_backups(self):
        env, calls, backup_dir = self._backup_env()
        # Pre-seed a stale backup plist that a fresh window must reap.
        stale = backup_dir / "com.claude-session-ping.backup-1432.plist"
        stale.write_text("<plist/>")
        start = int(__import__("datetime").datetime.now().replace(
            hour=14, minute=2, second=0, microsecond=0).timestamp())
        code, log = self.run_ping("14:02", usage=self._new_usage(start),
                                  extra_env=env)
        self.assertEqual(code, 0)
        self.assertFalse(stale.exists())
        # Reaped by label, not plist path: the file is already deleted when
        # the job is removed, and label removal works without it.
        self.assertIn("remove com.claude-session-ping.backup-1432",
                      calls.read_text())

    def test_backup_instance_cleanup_survives_its_own_sigterm(self):
        # launchctl remove/unload of the job a backup instance was started
        # from SIGTERMs that instance mid-run (Jul 17 19:31: the backup run
        # killed itself before rm/echo, leaving a stale plist that produced
        # "Unload failed: 5: Input/output error" at the next run). Plist
        # removal and the log line must complete before the self-kill, and
        # other stale jobs must be reaped before the instance's own.
        env, calls, backup_dir = self._backup_env(kill_on="backup-1437")
        own = backup_dir / "com.claude-session-ping.backup-1437.plist"
        own.write_text("<plist/>")
        other = backup_dir / "com.claude-session-ping.backup-1200.plist"
        other.write_text("<plist/>")
        start = int(__import__("datetime").datetime.now().replace(
            hour=14, minute=37, second=0, microsecond=0).timestamp())
        code, log = self.run_ping("14:37", usage=self._new_usage(start),
                                  backup_label="14:37", extra_env=env)
        self.assertFalse(own.exists())
        self.assertFalse(other.exists())
        self.assertIn("cleared backup com.claude-session-ping.backup-1437", log)
        self.assertIn("cleared backup com.claude-session-ping.backup-1200", log)
        lines = calls.read_text().splitlines()
        own_reap = next(i for i, l in enumerate(lines) if "1437" in l)
        other_reap = next(i for i, l in enumerate(lines) if "1200" in l)
        self.assertLess(other_reap, own_reap)

    def test_backup_suppressed_after_cutoff(self):
        env, calls, backup_dir = self._backup_env()
        # Window ends 23:05 -> fire 23:07 > cutoff 23:02 -> no plist written.
        resets = int(__import__("datetime").datetime.now().replace(
            hour=23, minute=5, second=0, microsecond=0).timestamp())
        code, log = self.run_ping("19:02", usage=self._no_new_usage(resets),
                                  extra_env=env)
        self.assertEqual(code, 0)
        plists = list(backup_dir.glob("com.claude-session-ping.backup-*.plist"))
        self.assertEqual(len(plists), 0)

    def test_reschedule_loads_new_label_before_unloading_old(self):
        # The core correctness property: launchctl unload SIGTERMs the running
        # backup process, so a re-chain must load the NEW (differently-labelled)
        # job before it unloads the OLD one — otherwise the chain dies mid-swap.
        env, calls, backup_dir = self._backup_env()
        old = backup_dir / "com.claude-session-ping.backup-1200.plist"
        old.write_text("<plist/>")
        # Window ends 14:30 -> fire 14:32, a different label than the stale 1200.
        resets = int(__import__("datetime").datetime.now().replace(
            hour=14, minute=30, second=0, microsecond=0).timestamp())
        code, log = self.run_ping("14:02", usage=self._no_new_usage(resets),
                                  extra_env=env)
        self.assertEqual(code, 0)
        # New label scheduled; the stale different-label job reaped.
        self.assertTrue((backup_dir / "com.claude-session-ping.backup-1432.plist").exists())
        self.assertFalse(old.exists())
        # Ordering: load of the new label precedes removal of the old one.
        lines = calls.read_text().splitlines()
        load_new = next(i for i, l in enumerate(lines)
                        if l.startswith("load") and "1432" in l)
        remove_old = next(i for i, l in enumerate(lines)
                          if l.startswith("remove") and "1200" in l)
        self.assertLess(load_new, remove_old)


if __name__ == "__main__":
    unittest.main()
