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


def fetch_usage_text(timeout: int = DEFAULT_TIMEOUT_SECONDS) -> tuple[str | None, str]:
    """The /usage prose, plus a short failure slug ("" when it succeeded).

    The slug is the only diagnostic the caller gets: stderr is discarded by
    the ping script, so a bare None left timeouts, crashes and parse failures
    indistinguishable in the log.
    """
    try:
        completed = subprocess.run(
            ["claude", "-p", "/usage", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,  # else claude stalls waiting on inherited stdin
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except FileNotFoundError:
        return None, "not_found"
    except OSError:
        return None, "os_error"
    if completed.returncode != 0:
        return None, f"exit_{completed.returncode}"
    return _result_from_stdout(completed.stdout)


def _result_from_stdout(stdout: str) -> tuple[str | None, str]:
    """Pull the result string out of stdout, tolerating non-JSON lines.

    stdout is not ours alone: on 2026-08-14 an MCP server appended
    "Client.listTools() called but server does not advertise tools
    capability" *after* the JSON, so decoding the whole buffer raised
    "Extra data" and 77 of 136 lookups silently fell back to the schedule.
    --output-format json emits the payload on a single line, so scan lines
    and take the first that decodes.
    """
    saw_payload = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        saw_payload = True
        result = payload.get("result")
        if isinstance(result, str):
            return result, ""
    return None, "no_result" if saw_payload else "bad_json"


def get_usage_with_reason(now: int, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> tuple[dict | None, str]:
    """Parsed usage, plus the slug explaining a None."""
    text, reason = fetch_usage_text(timeout)
    if not text:
        return None, reason
    usage = parse_usage_output(text, now)
    if usage is None:
        return None, "unparsed"
    return usage, ""


def get_usage(now: int, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict | None:
    """Parsed usage alone — the Telegram daemon's entry point."""
    return get_usage_with_reason(now, timeout)[0]


def read_prev_resets_at(path: str) -> int | None:
    """The previous run's recorded window reset, or None when unknown."""
    try:
        with open(path) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return None
    resets_at = state.get("resets_at")
    return resets_at if isinstance(resets_at, int) else None


def shell_lines(usage: dict | None, now: int, prev_resets_at: int | None = None,
                reason: str = "") -> list[str]:
    """KEY=VALUE lines for the zsh ping script to consume.

    `reason` is a bare slug (no spaces/quotes): the shell `eval`s these lines.
    """
    if not usage or not usage.get("session"):
        return ["USAGE_OK=0"] + ([f"USAGE_ERROR={reason}"] if reason else [])
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
        usage, reason = get_usage_with_reason(now)
    except Exception:  # noqa: BLE001 - never break the caller's ping
        usage, reason = None, "crashed"
    if usage and not usage.get("session"):
        reason = "no_session"
    # Read the state BEFORE the ping script overwrites it: it still holds the
    # previous window, against which newness is judged.
    prev_resets_at = read_prev_resets_at(STATE_FILE)
    for line in shell_lines(usage, now, prev_resets_at, reason):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
