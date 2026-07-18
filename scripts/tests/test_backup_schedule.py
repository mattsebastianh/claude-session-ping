"""Unit tests for scripts/backup_schedule.py (pure fire-time + cutoff logic)."""
import datetime
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backup_schedule import compute_backup

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "backup_schedule.py"


def epoch(y, mo, d, h, mi):
    return int(datetime.datetime(y, mo, d, h, mi).timestamp())


class TestComputeBackup(unittest.TestCase):
    def test_adds_buffer_to_resets_at(self):
        resets = epoch(2026, 7, 17, 14, 30)
        result = compute_backup(resets, 120, "23:02")
        self.assertEqual(result["hhmm"], "14:32")
        self.assertEqual(result["hour"], 14)
        self.assertEqual(result["minute"], 32)
        self.assertEqual(result["fire_epoch"], resets + 120)

    def test_suppressed_after_cutoff(self):
        resets = epoch(2026, 7, 17, 23, 5)  # +120s -> 23:07 > 23:02
        self.assertIsNone(compute_backup(resets, 120, "23:02"))

    def test_allowed_exactly_at_cutoff(self):
        resets = epoch(2026, 7, 17, 23, 0)  # +120s -> 23:02 == cutoff
        result = compute_backup(resets, 120, "23:02")
        self.assertEqual(result["hhmm"], "23:02")

    def test_suppressed_in_overnight_dead_zone(self):
        resets = epoch(2026, 7, 18, 0, 40)  # +120s -> 00:42, before 04:02
        self.assertIsNone(compute_backup(resets, 120, "23:02"))

    def test_allowed_near_lower_bound_when_no_target_covers(self):
        # Window ends 30s after the 04:02 target fired (absorbed into the old
        # window), so the backup at 04:04 is the only reopening left.
        resets = epoch(2026, 7, 17, 4, 2) + 30  # +120s -> 04:04:30
        result = compute_backup(resets, 120, "23:02")
        self.assertEqual(result["hhmm"], "04:04")


class TestTargetCollision(unittest.TestCase):
    def test_suppressed_when_backup_lands_on_a_target(self):
        # Regression, 2026-07-18: window 04:00-09:00 -> backup at 09:00+120s
        # = 09:02, exactly the scheduled 09:02 target. Both fired in the same
        # second, double-pinging and sending contradictory notifications.
        resets = epoch(2026, 7, 18, 9, 0)
        self.assertIsNone(compute_backup(resets, 120, "23:02"))

    def test_suppressed_when_target_falls_between_end_and_fire(self):
        # End 09:01, backup 09:03: the 09:02 target fires in between and
        # opens the fresh window itself; the backup would only duplicate it.
        resets = epoch(2026, 7, 18, 9, 1)
        self.assertIsNone(compute_backup(resets, 120, "23:02"))

    def test_suppressed_at_first_target_collision(self):
        resets = epoch(2026, 7, 17, 4, 0)  # +120s -> 04:02 == 04:02 target
        self.assertIsNone(compute_backup(resets, 120, "23:02"))

    def test_allowed_when_target_fired_before_window_end(self):
        # The 09:02 target fired 30s before the window ended -> absorbed;
        # the backup is still needed.
        resets = epoch(2026, 7, 18, 9, 2) + 30  # +120s -> 09:04:30
        result = compute_backup(resets, 120, "23:02")
        self.assertEqual(result["hhmm"], "09:04")

    def test_allowed_when_window_ends_well_after_target(self):
        resets = epoch(2026, 7, 18, 9, 25)  # +120s -> 09:27, next target 14:02
        result = compute_backup(resets, 120, "23:02")
        self.assertEqual(result["hhmm"], "09:27")

    def test_cli_suppresses_target_collision(self):
        resets = epoch(2026, 7, 18, 9, 0)
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

    def test_cli_prints_suppressed(self):
        resets = epoch(2026, 7, 17, 23, 5)
        out = subprocess.run(
            [sys.executable, str(HELPER), str(resets), "120", "23:02"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("BACKUP_OK=0", out)


if __name__ == "__main__":
    unittest.main()
