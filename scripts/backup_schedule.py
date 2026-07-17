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

FIRST_TARGET = "04:02"


def _minutes_of_day(hhmm: str) -> int:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return hour * 60 + minute


def compute_backup(
    resets_at: int,
    buffer: int,
    cutoff: str,
    first_target: str = FIRST_TARGET,
) -> dict | None:
    """Fire-time for a backup at resets_at+buffer, or None if outside the window.

    The allowed window is [first_target, cutoff] in local minutes-of-day. A
    backup opened after the cutoff would still be open at the next morning's
    04:02 target (5h window), wasting that slot; one before first_target sits
    in the overnight gap the schedule intentionally leaves uncovered.

    A past fire time needs no guard here: scheduling only ever runs when the
    caller saw WINDOW_IS_NEW=0 (the window is still open), so resets_at is in
    the future and fire_epoch is later still.
    """
    fire_epoch = resets_at + buffer
    dt = datetime.datetime.fromtimestamp(fire_epoch)
    minutes = dt.hour * 60 + dt.minute
    if not (_minutes_of_day(first_target) <= minutes <= _minutes_of_day(cutoff)):
        return None
    return {
        "fire_epoch": fire_epoch,
        "hhmm": dt.strftime("%H:%M"),
        "hour": dt.hour,
        "minute": dt.minute,
    }


def main(argv: list[str]) -> int:
    resets_at, buffer = int(argv[1]), int(argv[2])
    cutoff = argv[3]
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
