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


def format_day_time(epoch: int) -> str:
    """Format epoch seconds as a local "Thu 18:00" string (for resets days away)."""
    return datetime.datetime.fromtimestamp(epoch).strftime("%a %H:%M")


def humanize_delta(seconds: int) -> str:
    """Human-friendly duration like "2d 4h", "5h 53m", "42m", or "under a minute"."""
    if seconds < 60:
        return "under a minute"
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_usage_reply(usage: dict, now: int) -> str:
    """Combined live-usage reply; omits whichever of session/weekly is None."""
    lines = []
    session = usage.get("session")
    if session:
        resets = session["resets_at"]
        lines.append(
            f"📊 Session: {session['pct']:.0f}% used — resets {format_time(resets)} "
            f"({humanize_delta(resets - now)} left)"
        )
    weekly = usage.get("weekly")
    if weekly:
        resets = weekly["resets_at"]
        lines.append(
            f"📅 Weekly: {weekly['pct']:.0f}% used — resets {format_day_time(resets)} "
            f"({humanize_delta(resets - now)} left)"
        )
    return "\n".join(lines)


def extract_output_text(result: dict) -> str | None:
    """Pull the assistant text out of an OpenAI Responses API result.

    The output list may contain non-message items (e.g. "reasoning") before
    the message, so scan for the first message/output_text pair.
    """
    for item in result.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "output_text":
                return part.get("text", "").strip()
    return None


def _target_epoch(base_epoch: int, hhmm: str) -> int:
    dt = datetime.datetime.fromtimestamp(base_epoch)
    hour, minute = (int(x) for x in hhmm.split(":"))
    target = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return int(target.timestamp())


def current_window_start(now: int, targets: list[str] = TARGETS) -> int:
    """Start of the schedule window containing `now`, or 0 if none is active.

    A window opens at each target time and stays active WINDOW_SECONDS.
    Checks yesterday's targets too, in case a window spans midnight.
    """
    latest = 0
    for day_offset in (-1, 0):
        base = now + day_offset * 86400
        for hhmm in targets:
            ts = _target_epoch(base, hhmm)
            if ts <= now < ts + WINDOW_SECONDS:
                latest = max(latest, ts)
    return latest


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
    "next_next_start": ("next next", "after that", "second next", "one after"),
    "next_start": ("next session", "next start", "next window", "reset", "next ping", "when can i"),
    "window_open": ("opened", "open", "began", "since when"),
    "window_end": ("end", "ending", "finish", "over"),
    "usage": ("usage", "percent", "%", "how much", "elapsed", "weekly", "limit", "quota", "used", "remaining"),
}

INTENT_ORDER = ("next_next_start", "next_start", "window_open", "window_end", "usage")

# Keywords that are short/common enough to false-positive as substrings of
# unrelated words (e.g. "end" inside "weekend", "over" inside "recover").
# These are matched with word boundaries instead of plain substring `in`.
_WORD_BOUNDARY_KEYWORDS = {"end", "ending", "finish", "over", "open", "opened", "began", "used", "limit"}


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
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].lstrip()
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if value[:1] in ("'", '"') and len(value) >= 2 and value.endswith(value[0]):
            value = value[1:-1]
        else:
            # Unquoted: zsh treats " #..." as a trailing comment; agree with it.
            value = value.split(" #", 1)[0].rstrip()
        env[key] = value
    return env
