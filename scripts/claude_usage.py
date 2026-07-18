#!/usr/bin/env python3
"""Fetch and expose Claude's real usage window.

Invokes `claude -p "/usage"`, which is served locally with no API call
(num_turns: 0), so it neither consumes quota nor opens a session window.

Every failure returns None so callers fall back to schedule-based behavior.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from usage_lib import (  # noqa: E402
    WEEKLY_WARN_PERCENT,
    derive_window_start,
    parse_usage_output,
    window_is_new,
)

DEFAULT_TIMEOUT_SECONDS = 30
# Same default as scripts/claude_session_ping.sh and telegram_qa_daemon.py.
STATE_FILE = os.environ.get(
    "CLAUDE_SESSION_PING_STATE_FILE",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".claude-session-ping",
        "state.json",
    ),
)


def fetch_usage_text(timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str | None:
    try:
        completed = subprocess.run(
            ["claude", "-p", "/usage", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,  # else claude stalls waiting on inherited stdin
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    result = payload.get("result")
    return result if isinstance(result, str) else None


def get_usage(now: int, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict | None:
    text = fetch_usage_text(timeout)
    if not text:
        return None
    return parse_usage_output(text, now)


def read_prev_resets_at(path: str) -> int | None:
    """The previous run's recorded window reset, or None when unknown."""
    try:
        with open(path) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return None
    resets_at = state.get("resets_at")
    return resets_at if isinstance(resets_at, int) else None


def shell_lines(usage: dict | None, now: int, prev_resets_at: int | None = None) -> list[str]:
    """KEY=VALUE lines for the zsh ping script to consume."""
    if not usage or not usage.get("session"):
        return ["USAGE_OK=0"]
    session = usage["session"]
    window_start = derive_window_start(session["resets_at"])
    is_new = window_is_new(now, window_start, prev_resets_at)
    lines = [
        "USAGE_OK=1",
        f"SESSION_PCT={session['pct']:.0f}",
        f"SESSION_RESETS_AT={session['resets_at']}",
        f"WINDOW_START={window_start}",
        f"WINDOW_IS_NEW={1 if is_new else 0}",
    ]
    weekly = usage.get("weekly")
    if weekly:
        lines.append(f"WEEKLY_PCT={weekly['pct']:.0f}")
        lines.append(f"WEEKLY_WARN={1 if weekly['pct'] >= WEEKLY_WARN_PERCENT else 0}")
    return lines


def main() -> int:
    now = int(time.time())
    try:
        usage = get_usage(now)
    except Exception:  # noqa: BLE001 - never break the caller's ping
        usage = None
    # Read the state BEFORE the ping script overwrites it: it still holds the
    # previous window, against which newness is judged.
    prev_resets_at = read_prev_resets_at(STATE_FILE)
    for line in shell_lines(usage, now, prev_resets_at):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
