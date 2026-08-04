"""Unit tests for scripts/backup_schedule.py (pure fire-time + cutoff logic)."""
import datetime
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backup_schedule import DEFAULT_CUTOFF, FIRST_TARGET, compute_backup
from telegram_qa_lib import WINDOW_SECONDS

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "backup_schedule.py"


def epoch(y, mo, d, h, mi):
    return int(datetime.datetime(y, mo, d, h, mi).timestamp())


class TestComputeBackup(unittest.TestCase):
    def test_adds_buffer_to_resets_at(self):
        resets = epoch(2026, 7, 17, 14, 30)
        result = compute_backup(resets, 120, DEFAULT_CUTOFF)
        self.assertEqual(result["hhmm"], "14:32")
        self.assertEqual(result["hour"], 14)
        self.assertEqual(result["minute"], 32)
        self.assertEqual(result["fire_epoch"], resets + 120)

    def test_suppressed_after_a_same_day_cutoff(self):
        # Non-wrapping cutoff (first_target < cutoff): the plain interval path.
        resets = epoch(2026, 7, 17, 23, 5)  # +120s -> 23:07 > 23:02
        self.assertIsNone(compute_backup(resets, 120, "23:02"))

    def test_allowed_exactly_at_a_same_day_cutoff(self):
        resets = epoch(2026, 7, 17, 23, 0)  # +120s -> 23:02 == cutoff
        result = compute_backup(resets, 120, "23:02")
        self.assertEqual(result["hhmm"], "23:02")

    def test_allowed_late_evening_under_default_cutoff(self):
        # 23:32 is before midnight, so it is inside the wrapped fire window.
        resets = epoch(2026, 7, 17, 23, 30)
        result = compute_backup(resets, 120, DEFAULT_CUTOFF)
        self.assertEqual(result["hhmm"], "23:32")

    def test_allowed_near_lower_bound_when_no_target_covers(self):
        # Window ends 30s after the 07:02 target fired (absorbed into the old
        # window), so the backup at 07:04 is the only reopening left.
        resets = epoch(2026, 7, 17, 7, 2) + 30  # +120s -> 07:04:30
        result = compute_backup(resets, 120, DEFAULT_CUTOFF)
        self.assertEqual(result["hhmm"], "07:04")


class TestOvernightGap(unittest.TestCase):
    """The fire window wraps midnight: 07:02-01:59 allowed, 02:00-07:01 not.

    The point of the gap is that every window a backup opens has closed before
    the 07:02 target, so the day's first ping opens a real window instead of
    being absorbed into an overnight one.
    """

    def test_allowed_exactly_at_the_wrapped_cutoff(self):
        resets = epoch(2026, 7, 18, 1, 57)  # +120s -> 01:59 == cutoff
        result = compute_backup(resets, 120, DEFAULT_CUTOFF)
        self.assertEqual(result["hhmm"], "01:59")

    def test_suppressed_one_minute_past_the_wrapped_cutoff(self):
        resets = epoch(2026, 7, 18, 1, 58)  # +120s -> 02:00
        self.assertIsNone(compute_backup(resets, 120, DEFAULT_CUTOFF))

    def test_allowed_after_midnight_inside_the_wrap(self):
        resets = epoch(2026, 7, 18, 0, 40)  # +120s -> 00:42
        result = compute_backup(resets, 120, DEFAULT_CUTOFF)
        self.assertEqual(result["hhmm"], "00:42")

    def test_evening_windows_own_reopening_lands_in_the_gap(self):
        # The 22:02 window ends 03:02 -> fire 03:04, inside the gap. Overnight
        # coverage is intentionally not re-chained; the day restarts at 07:02.
        resets = epoch(2026, 7, 18, 3, 2)
        self.assertIsNone(compute_backup(resets, 120, DEFAULT_CUTOFF))

    def test_nothing_fires_anywhere_in_the_gap(self):
        for hour, minute in ((2, 0), (3, 4), (4, 30), (6, 0), (6, 59)):
            with self.subTest(hour=hour, minute=minute):
                resets = epoch(2026, 7, 18, hour, minute) - 120
                self.assertIsNone(compute_backup(resets, 120, DEFAULT_CUTOFF))

    def test_last_allowed_fire_closes_before_the_first_target(self):
        # The invariant the cutoff exists for, asserted on real epochs.
        resets = epoch(2026, 7, 18, 1, 57)
        result = compute_backup(resets, 120, DEFAULT_CUTOFF)
        first_hh, first_mm = (int(x) for x in FIRST_TARGET.split(":"))
        first_target_epoch = epoch(2026, 7, 18, first_hh, first_mm)
        self.assertLess(result["fire_epoch"] + WINDOW_SECONDS, first_target_epoch)


class TestTargetCollision(unittest.TestCase):
    def test_suppressed_when_backup_lands_on_a_target(self):
        # Regression, 2026-07-18 (times shifted to the current schedule): a
        # window ending 12:00 -> backup at 12:00+120s = 12:02, exactly the
        # scheduled 12:02 target. Both fired in the same second, double-pinging
        # and sending contradictory notifications.
        resets = epoch(2026, 7, 18, 12, 0)
        self.assertIsNone(compute_backup(resets, 120, "23:02"))

    def test_suppressed_when_target_falls_between_end_and_fire(self):
        # End 12:01, backup 12:03: the 12:02 target fires in between and
        # opens the fresh window itself; the backup would only duplicate it.
        resets = epoch(2026, 7, 18, 12, 1)
        self.assertIsNone(compute_backup(resets, 120, "23:02"))

    def test_suppressed_at_first_target_collision(self):
        resets = epoch(2026, 7, 17, 7, 0)  # +120s -> 07:02 == 07:02 target
        self.assertIsNone(compute_backup(resets, 120, "23:02"))

    def test_allowed_when_target_fired_before_window_end(self):
        # The 12:02 target fired 30s before the window ended -> absorbed;
        # the backup is still needed.
        resets = epoch(2026, 7, 18, 12, 2) + 30  # +120s -> 12:04:30
        result = compute_backup(resets, 120, "23:02")
        self.assertEqual(result["hhmm"], "12:04")

    def test_allowed_when_window_ends_well_after_target(self):
        resets = epoch(2026, 7, 18, 12, 25)  # +120s -> 12:27, next target 17:02
        result = compute_backup(resets, 120, "23:02")
        self.assertEqual(result["hhmm"], "12:27")

    def test_cli_suppresses_target_collision(self):
        resets = epoch(2026, 7, 18, 12, 0)
        out = subprocess.run(
            [sys.executable, str(HELPER), str(resets), "120", "23:02"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("BACKUP_OK=0", out)

    def test_cli_prints_schedule_lines(self):
        resets = epoch(2026, 7, 17, 14, 30)
        out = subprocess.run(
            [sys.executable, str(HELPER), str(resets), "120", "23:02"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("BACKUP_OK=1", out)
        self.assertIn("BACKUP_HHMM=14:32", out)
        self.assertIn("BACKUP_HOUR=14", out)
        self.assertIn("BACKUP_MINUTE=32", out)

    def test_suppressed_when_a_previous_day_target_covers_reopening(self):
        # Large buffer + a wrapped fire time: the covering target sits on the
        # previous calendar day (window ends 21:00, fire 01:30, and the 22:02
        # target in between already reopens coverage).
        resets = epoch(2026, 7, 17, 21, 0)
        self.assertIsNone(compute_backup(resets, 16200, DEFAULT_CUTOFF))

    def test_cli_prints_suppressed(self):
        resets = epoch(2026, 7, 17, 23, 5)
        out = subprocess.run(
            [sys.executable, str(HELPER), str(resets), "120", "23:02"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("BACKUP_OK=0", out)


if __name__ == "__main__":
    unittest.main()
