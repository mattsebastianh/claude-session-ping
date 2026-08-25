"""Pure, network-free logic for the Telegram Q&A daemon.

Kept separate from telegram_qa_daemon.py so the scheduling/parsing logic
can be unit tested without hitting Telegram or OpenRouter.
"""
from __future__ import annotations

import datetime
import re
import socket
import ssl

TARGETS = ["07:02", "12:02", "17:02", "22:02"]
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


def format_status_reply(usage: dict | None, window_start: int, status: str, now: int) -> str:
    """Whole-picture status: one labeled fact per line.

    Answers the broad "session info"/"overview"/"status" questions locally,
    so the most common question costs no LLM call and is formatted the same
    way every time (the model-written version drifted between run-on prose
    and lines depending on phrasing).
    """
    lines = []
    if window_start:
        lines.append(
            f"🪟 Window: {format_time(window_start)}–{format_time(window_end(window_start))} "
            f"({usage_percent(window_start, now):.0f}% elapsed)"
        )
    else:
        lines.append("🪟 Window: none active")

    icon = "✅" if status == "success" else "⚠️"
    lines.append(f"{icon} Last ping: {status}")

    starts = next_start_times(now)
    if starts:
        lines.append(f"⏭️ Next start: {format_time(starts[0])}")
    if len(starts) > 1:
        lines.append(f"⏭️ Then: {format_time(starts[1])}")

    if usage:
        lines.append(format_usage_reply(usage, now))
    else:
        lines.append("⚠️ Live usage unavailable — window figures are schedule estimates")
    return "\n".join(line for line in lines if line)


def usage_prompt_line(usage: dict | None) -> str:
    """System-prompt sentence of live usage (trailing space), or "" if unavailable."""
    if not usage:
        return ""
    parts = []
    session = usage.get("session")
    if session:
        parts.append(f"session {session['pct']:.0f}% used, resets {format_time(session['resets_at'])}")
    weekly = usage.get("weekly")
    if weekly:
        parts.append(f"weekly {weekly['pct']:.0f}% used, resets {format_day_time(weekly['resets_at'])}")
    if not parts:
        return ""
    return "Live usage: " + "; ".join(parts) + ". "


def _is_timeout(exc: BaseException) -> bool:
    # socket.timeout is listed separately from TimeoutError on purpose: the two
    # were only merged in Python 3.10, and launchd runs this daemon under the
    # system 3.9, where socket.timeout is a bare OSError subclass. urllib
    # raises socket.timeout for a read timeout, so checking TimeoutError alone
    # missed every DarkWake timeout on that runtime — the exact false alarms
    # this filter exists to stop (seen again 2026-08-25 14:35).
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    # Over HTTPS, the same dead-socket-after-DarkWake timeout can surface as
    # ssl.SSLError instead of socket.timeout/TimeoutError (e.g. during the
    # TLS handshake, or a post-handshake read) - it's an OSError subclass,
    # not a TimeoutError subclass, so it needs its own check here.
    if isinstance(exc, ssl.SSLError) and "timed out" in str(exc).lower():
        return True
    return False


def counts_toward_outage(exc: BaseException) -> bool:
    """Whether a getUpdates failure suggests a real outage worth alerting on.

    A read timeout does not: the 30s long poll times out routinely, and on a
    Mac that sleeps, every DarkWake surfaces the dead socket as a timeout.
    Treating those as an outage produced a dozen false alarms in one night.
    """
    if _is_timeout(exc):
        return False
    reason = getattr(exc, "reason", None)
    if reason is not None and _is_timeout(reason):
        return False
    return True


def next_failure_count(current: int, status: str) -> int:
    """Consecutive alert-worthy failures after a poll with `status`.

    "transient" holds the count rather than resetting it, so a genuine
    outage that starts during sleep still reaches the alert threshold.
    """
    if status == "ok":
        return 0
    if status == "error":
        return current + 1
    return current


def extract_chat_completion_text(result: dict) -> str | None:
    """Pull the assistant text out of an OpenRouter chat-completions result."""
    choices = result.get("choices") or []
    if not choices:
        return None
    content = choices[0].get("message", {}).get("content")
    if not content:
        return None
    return content.strip()


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
    # Broad "tell me everything" phrasings. Matched LAST (see INTENT_ORDER) so
    # a question naming one specific facet still wins: "quota status" is a
    # quota question, while a bare "status" or "session info" is the whole
    # picture.
    "status": ("overview", "info", "status", "summary", "how are things", "full picture"),
    "next_next_start": ("next next", "after that", "second next", "one after"),
    "next_start": ("next session", "next start", "next window", "reset", "next ping", "when can i"),
    "window_open": ("opened", "open", "began", "since when"),
    "window_end": ("end", "ending", "finish", "over"),
    "usage": ("usage", "percent", "%", "how much", "elapsed", "weekly", "limit", "quota", "used", "remaining"),
}

INTENT_ORDER = ("next_next_start", "next_start", "window_open", "window_end", "usage", "status")

# Keywords that are short/common enough to false-positive as substrings of
# unrelated words (e.g. "end" inside "weekend", "over" inside "recover").
# These are matched with word boundaries instead of plain substring `in`.
_WORD_BOUNDARY_KEYWORDS = {"end", "ending", "finish", "over", "open", "opened", "began", "used", "limit"}


# Telegram slash commands, surfaced in the client's command menu. Each maps
# to an intent that is answered locally, so a shortcut never costs an LLM call.
BOT_COMMANDS = (
    ("status", "Full session overview"),
    ("usage", "Session + weekly usage"),
    ("window", "When the current window opened"),
    ("ends", "When the current window ends"),
    ("next", "Next session start time"),
)

_COMMAND_INTENTS = {
    "status": "status",
    "usage": "usage",
    "window": "window_open",
    "ends": "window_end",
    "next": "next_start",
}


def command_intent(text: str) -> str | None:
    """Intent for a Telegram slash command, or None if `text` isn't one.

    Accepts the `/cmd@BotName` form Telegram uses in groups, and ignores any
    trailing arguments so "/usage please" still resolves.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    word = stripped.split()[0][1:]
    name = word.split("@", 1)[0].lower()
    return _COMMAND_INTENTS.get(name)


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
