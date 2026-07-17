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
        now = epoch(2026, 7, 17, 14, 2)
        result = compute_backup(resets, 120, "23:02", now)
        self.assertEqual(result["hhmm"], "14:32")
        self.assertEqual(result["hour"], 14)
        self.assertEqual(result["minute"], 32)
        self.assertEqual(result["fire_epoch"], resets + 120)

    def test_suppressed_after_cutoff(self):
        resets = epoch(2026, 7, 17, 23, 5)  # +120s -> 23:07 > 23:02
        now = epoch(2026, 7, 17, 19, 2)
        self.assertIsNone(compute_backup(resets, 120, "23:02", now))

    def test_allowed_exactly_at_cutoff(self):
        resets = epoch(2026, 7, 17, 23, 0)  # +120s -> 23:02 == cutoff
        now = epoch(2026, 7, 17, 19, 2)
        result = compute_backup(resets, 120, "23:02", now)
        self.assertEqual(result["hhmm"], "23:02")

    def test_suppressed_in_overnight_dead_zone(self):
        resets = epoch(2026, 7, 18, 0, 40)  # +120s -> 00:42, before 04:02
        now = epoch(2026, 7, 17, 19, 2)
        self.assertIsNone(compute_backup(resets, 120, "23:02", now))

    def test_allowed_at_lower_bound(self):
        resets = epoch(2026, 7, 17, 4, 0)  # +120s -> 04:02 == first target
        now = epoch(2026, 7, 17, 3, 40)
        result = compute_backup(resets, 120, "23:02", now)
        self.assertEqual(result["hhmm"], "04:02")

    def test_cli_prints_schedule_lines(self):
        resets = epoch(2026, 7, 17, 14, 30)
        now = epoch(2026, 7, 17, 14, 2)
        out = subprocess.run(
            [sys.executable, str(HELPER), str(resets), "120", "23:02", str(now)],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("BACKUP_OK=1", out)
        self.assertIn("BACKUP_HHMM=14:32", out)
        self.assertIn("BACKUP_HOUR=14", out)
        self.assertIn("BACKUP_MINUTE=32", out)

    def test_cli_prints_suppressed(self):
        resets = epoch(2026, 7, 17, 23, 5)
        now = epoch(2026, 7, 17, 19, 2)
        out = subprocess.run(
            [sys.executable, str(HELPER), str(resets), "120", "23:02", str(now)],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("BACKUP_OK=0", out)


if __name__ == "__main__":
    unittest.main()
