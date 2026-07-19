"""Pure, network-free parsing of `claude -p "/usage"` output.

The output is undocumented human-readable prose, so every function here is
defensive: anything unrecognized yields None and the caller falls back to
schedule-based behavior.
"""
from __future__ import annotations

import datetime
import re

from telegram_qa_lib import WINDOW_SECONDS

WEEKLY_WARN_PERCENT = 80
# Anthropic anchors the reported window start to a coarse boundary that can
# precede the opening request by several minutes (a 04:04:49 ping produced a
# window reported as 04:00-09:00), so "new" must tolerate that anchoring plus
# the usage lookup's own latency. Deliberately NOT tied to the launchd grace
# window: a window opens when the ping actually fires, so a late fire moves
# window_start along with `now` and does not widen this gap.
MAX_NEW_WINDOW_AGE_SECONDS = 40 * 60
# Displayed reset times round by up to a minute (a window ending 14:30 was
# followed by one reported as starting 14:29).
PREV_RESET_SLACK_SECONDS = 120

# "Current session: 0% used · resets Jul 15 at 7:09pm (America/Guayaquil)"
# Separator is U+00B7. Time may omit minutes ("12am").
_LINE = re.compile(
    r"Current (?P<kind>session|week[^:]*): (?P<pct>\d+(?:\.\d+)?)% used"
    r".*?resets (?P<mon>[A-Z][a-z]{2}) (?P<day>\d{1,2})"
    r" at (?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?(?P<ampm>am|pm)",
)

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )
}


def _reset_epoch(match: re.Match, now: int) -> int | None:
    month = _MONTHS.get(match.group("mon"))
    if month is None:
        return None
    hour = int(match.group("hour")) % 12
    if match.group("ampm") == "pm":
        hour += 12
    minute = int(match.group("minute") or 0)
    day = int(match.group("day"))
    year = datetime.datetime.fromtimestamp(now).year
    # The year is absent from the output; pick the first candidate that isn't
    # implausibly in the past, so a Dec->Jan reset rolls into the next year.
    for candidate_year in (year, year + 1):
        try:
            dt = datetime.datetime(candidate_year, month, day, hour, minute)
        except ValueError:
            return None
        if int(dt.timestamp()) >= now - WINDOW_SECONDS:
            return int(dt.timestamp())
    return None


def parse_usage_output(text: str, now: int) -> dict | None:
    """Extract session/weekly limits from `/usage` prose.

    Returns None when nothing parses, so callers fall back to the schedule.
    """
    session = None
    weekly = None
    for match in _LINE.finditer(text):
        resets_at = _reset_epoch(match, now)
        if resets_at is None:
            continue
        entry = {"pct": float(match.group("pct")), "resets_at": resets_at}
        if match.group("kind") == "session":
            session = entry
        elif weekly is None or entry["pct"] > weekly["pct"]:
            weekly = entry
    if session is None and weekly is None:
        return None
    return {"session": session, "weekly": weekly}


def derive_window_start(resets_at: int) -> int:
    """The 5-hour window's start, derived from when it resets."""
    return resets_at - WINDOW_SECONDS


def window_is_new(now: int, window_start: int, prev_resets_at: int | None = None) -> bool:
    """Whether this run's ping (not earlier activity) opened the window.

    A window opened by this run started recently — but never before the
    previously recorded window's end, since session windows don't overlap.
    Clock proximity alone misclassifies: start-time anchoring plus a late
    launchd fire pushed a genuinely new window 290s from `now` on 2026-07-18.
    """
    if now - window_start > MAX_NEW_WINDOW_AGE_SECONDS:
        return False
    if prev_resets_at is not None and window_start < prev_resets_at - PREV_RESET_SLACK_SECONDS:
        return False
    return True
