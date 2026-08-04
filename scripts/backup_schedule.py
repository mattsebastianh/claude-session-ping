#!/usr/bin/env python3
"""Pure fire-time and cutoff logic for the keepalive backup ping.

Given when the current Claude window resets, decide whether — and at what
local time — to schedule a one-shot "backup" ping just after it ends. Kept
network-free and side-effect-free so it can be unit tested; the shell script
calls the CLI form the same way it calls claude_usage.py.
"""
from __future__ import annotations

import datetime
import sys

from telegram_qa_lib import TARGETS

FIRST_TARGET = "07:02"
# Latest local time a backup may open a window. A window runs 5h, so 01:59
# closes at 06:59 — clear of the 07:02 first target, which then opens the day
# itself. 02:02 would close at exactly 07:02 and swallow that target.
DEFAULT_CUTOFF = "01:59"


def _minutes_of_day(hhmm: str) -> int:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return hour * 60 + minute


def _in_fire_window(minutes: int, first_target: str, cutoff: str) -> bool:
    """Is a fire time at `minutes` of the local day inside [first_target, cutoff]?

    The interval wraps past midnight whenever cutoff < first_target (the real
    case: 07:02 → 01:59), so it cannot be a single chained comparison.
    """
    first = _minutes_of_day(first_target)
    cut = _minutes_of_day(cutoff)
    if first <= cut:
        return first <= minutes <= cut
    return minutes >= first or minutes <= cut


def _target_covers_reopening(resets_at: int, fire_epoch: int, targets: list[str]) -> bool:
    """A scheduled target firing in [window end, backup fire] opens the fresh
    window itself, making the backup a duplicate. 2026-07-18: window
    04:00-09:00 produced a backup at 09:00+120s = 09:02 — the exact second of
    the scheduled 09:02 target — and both fired, double-pinging and sending
    contradictory notifications.

    Yesterday's targets are checked too: a fire time just past midnight is a
    legal outcome now that the fire window wraps, and with a large buffer the
    covering target can sit on the previous calendar day."""
    fire_dt = datetime.datetime.fromtimestamp(fire_epoch)
    for day_offset in (-1, 0):
        base = fire_dt + datetime.timedelta(days=day_offset)
        for target in targets:
            hour, minute = (int(x) for x in target.split(":"))
            target_epoch = int(
                base.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()
            )
            if resets_at <= target_epoch <= fire_epoch:
                return True
    return False


def compute_backup(
    resets_at: int,
    buffer: int,
    cutoff: str,
    first_target: str = FIRST_TARGET,
    targets: list[str] = TARGETS,
) -> dict | None:
    """Fire-time for a backup at resets_at+buffer, or None if outside the window.

    The allowed window is [first_target, cutoff] in local minutes-of-day,
    wrapping past midnight (07:02 → 01:59). The cutoff exists to guarantee the
    5h frame a backup opens has fully closed before the first target: at 01:59
    it ends 06:59, so the 07:02 ping opens the day for real instead of being
    absorbed. Everything from the cutoff to first_target is the overnight gap
    the schedule intentionally leaves uncovered — which is why the 22:02
    window's own reopening (ending 03:02, firing 03:04) is suppressed.

    A past fire time needs no guard here: scheduling only ever runs when the
    caller saw WINDOW_IS_NEW=0 (the window is still open), so resets_at is in
    the future and fire_epoch is later still.
    """
    fire_epoch = resets_at + buffer
    dt = datetime.datetime.fromtimestamp(fire_epoch)
    minutes = dt.hour * 60 + dt.minute
    if not _in_fire_window(minutes, first_target, cutoff):
        return None
    if _target_covers_reopening(resets_at, fire_epoch, targets):
        return None
    return {
        "fire_epoch": fire_epoch,
        "hhmm": dt.strftime("%H:%M"),
        "hour": dt.hour,
        "minute": dt.minute,
    }


def main(argv: list[str]) -> int:
    resets_at, buffer = int(argv[1]), int(argv[2])
    cutoff = argv[3] if len(argv) > 3 else DEFAULT_CUTOFF
    result = compute_backup(resets_at, buffer, cutoff)
    if result is None:
        print("BACKUP_OK=0")
        return 0
    print("BACKUP_OK=1")
    print(f"BACKUP_HHMM={result['hhmm']}")
    print(f"BACKUP_HOUR={result['hour']}")
    print(f"BACKUP_MINUTE={result['minute']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
