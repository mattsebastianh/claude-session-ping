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
    NEW_WINDOW_TOLERANCE_SECONDS,
    WEEKLY_WARN_PERCENT,
    derive_window_start,
    parse_usage_output,
)

DEFAULT_TIMEOUT_SECONDS = 30


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


def shell_lines(usage: dict | None, now: int) -> list[str]:
    """KEY=VALUE lines for the zsh ping script to consume."""
    if not usage or not usage.get("session"):
        return ["USAGE_OK=0"]
    session = usage["session"]
    window_start = derive_window_start(session["resets_at"])
    is_new = abs(now - window_start) <= NEW_WINDOW_TOLERANCE_SECONDS
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
    for line in shell_lines(usage, now):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
