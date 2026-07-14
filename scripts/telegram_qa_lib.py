"""Pure, network-free logic for the Telegram Q&A daemon.

Kept separate from telegram_qa_daemon.py so the scheduling/parsing logic
can be unit tested without hitting Telegram or OpenAI.
"""
from __future__ import annotations

import datetime
import re

TARGETS = ["04:00", "09:00", "14:00", "19:00"]
WINDOW_SECONDS = 5 * 60 * 60


def usage_percent(window_start: int, now: int) -> float:
    """% of the 5-hour window elapsed since window_start, clamped to [0, 100]."""
    if window_start <= 0:
        return 0.0
    elapsed = now - window_start
    pct = (elapsed / WINDOW_SECONDS) * 100
    return max(0.0, min(100.0, pct))


def window_end(window_start: int) -> int:
    """Epoch seconds when the current window closes."""
    return window_start + WINDOW_SECONDS


def format_time(epoch: int) -> str:
    """Format epoch seconds as a local HH:MM string."""
    return datetime.datetime.fromtimestamp(epoch).strftime("%H:%M")


def _target_epoch(base_epoch: int, hhmm: str) -> int:
    dt = datetime.datetime.fromtimestamp(base_epoch)
    hour, minute = (int(x) for x in hhmm.split(":"))
    target = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return int(target.timestamp())


def next_start_times(now: int, targets: list[str] = TARGETS, count: int = 2) -> list[int]:
    """The next `count` schedule start times strictly after `now`, in order."""
    candidates = []
    for day_offset in (0, 1):
        base = now + day_offset * 86400
        for hhmm in targets:
            ts = _target_epoch(base, hhmm)
            if ts > now:
                candidates.append(ts)
    candidates.sort()
    return candidates[:count]


INTENT_KEYWORDS = {
    "next_next_start": ("next next", "after that", "second next"),
    "next_start": ("next session", "next start", "next window"),
    "window_end": ("end", "ending", "finish", "over"),
    "usage": ("usage", "percent", "%", "how much"),
}

INTENT_ORDER = ("next_next_start", "next_start", "window_end", "usage")

# Keywords that are short/common enough to false-positive as substrings of
# unrelated words (e.g. "end" inside "weekend", "over" inside "recover").
# These are matched with word boundaries instead of plain substring `in`.
_WORD_BOUNDARY_KEYWORDS = {"end", "ending", "finish", "over"}


def match_intent(text: str) -> str:
    """Return one of INTENT_ORDER's keys, or "none" if nothing matches."""
    lowered = text.lower()
    for intent in INTENT_ORDER:
        for keyword in INTENT_KEYWORDS[intent]:
            if keyword in _WORD_BOUNDARY_KEYWORDS:
                if re.search(rf"\b{re.escape(keyword)}\b", lowered):
                    return intent
            elif keyword in lowered:
                return intent
    return "none"


def parse_env_text(text: str) -> dict[str, str]:
    """Parse simple KEY=VALUE lines (like a shell env file), ignoring blanks/comments."""
    env: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        env[key] = value
    return env
