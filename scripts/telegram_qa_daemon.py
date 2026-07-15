#!/usr/bin/env python3
"""Long-polling Telegram Q&A daemon for claude-session-ping.

Answers questions about the current keepalive schedule using the shared
state file written by scripts/claude_session_ping.sh, falling back to an
OpenAI chat completion for anything it doesn't recognize.

Requires only the Python 3 standard library.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_usage import get_usage  # noqa: E402
from telegram_qa_lib import (  # noqa: E402
    current_window_start,
    extract_output_text,
    format_time,
    humanize_delta,
    match_intent,
    next_start_times,
    parse_env_text,
    usage_percent,
    window_end,
)
from usage_lib import derive_window_start  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = Path(os.environ.get("CLAUDE_SESSION_PING_ENV_FILE", str(ROOT / ".env")))
STATE_FILE = Path(os.environ.get("CLAUDE_SESSION_PING_STATE_FILE", str(ROOT / ".claude-session-ping" / "state.json")))
LOG_FILE = Path(os.environ.get(
    "CLAUDE_SESSION_PING_TELEGRAM_BOT_LOG",
    str(Path(__file__).resolve().parents[1] / "logs" / "claude-session-ping-telegram-bot.log"),
))

POLL_TIMEOUT_SECONDS = 30
DEFAULT_OPENAI_MODEL = "gpt-5-nano"
MAX_GETUPDATES_FAILURES_BEFORE_ALERT = 3


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a") as fh:
        fh.write(f"[{timestamp}] {message}\n")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    if ENV_FILE.exists():
        env.update(parse_env_text(ENV_FILE.read_text()))
    return env


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"window_start": 0, "window_label": "unknown", "status": "unknown"}


def telegram_request(token: str, method: str, params: dict, timeout: int) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def send_message(token: str, chat_id: str, text: str) -> None:
    try:
        telegram_request(token, "sendMessage", {"chat_id": chat_id, "text": text}, timeout=10)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        log(f"sendMessage failed: {exc}")


def maybe_notify_poll_failure(token: str, chat_id: str, failure_count: int, exc: str) -> None:
    if failure_count != MAX_GETUPDATES_FAILURES_BEFORE_ALERT:
        return
    warning = (
        f"Telegram polling has failed {failure_count} times in a row; "
        f"last error: {exc}. I will notify you if it continues."
    )
    log(warning)
    send_message(token, chat_id, warning)


def get_updates(token: str, offset: int | None) -> tuple[list[dict], bool, str | None]:
    params: dict = {"timeout": POLL_TIMEOUT_SECONDS}
    if offset is not None:
        params["offset"] = offset
    try:
        result = telegram_request(token, "getUpdates", params, timeout=POLL_TIMEOUT_SECONDS + 10)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        log(f"getUpdates failed: {exc}")
        time.sleep(5)
        return [], True, str(exc)
    return result.get("result", []), False, None


def openai_answer(api_key: str, model: str, state: dict, question: str, window_start: int = 0) -> str:
    now = int(time.time())
    starts = next_start_times(now)
    if window_start:
        window_desc = (
            f"opened_at={format_time(window_start)}, "
            f"ends_at={format_time(window_end(window_start))}, "
            f"elapsed={usage_percent(window_start, now):.0f}%"
        )
    else:
        window_desc = "none active"
    system_prompt = (
        "You are a terse status bot for a Claude Code keepalive scheduler. "
        "Daily windows open at 04:00, 09:00, 14:00, 19:00 and each stays active for 5 hours. "
        f"Current window: {window_desc}, "
        f"last_ping_status={state.get('status')}. "
        f"Next start: {format_time(starts[0]) if starts else 'unknown'}. "
        f"Next next start: {format_time(starts[1]) if len(starts) > 1 else 'unknown'}. "
        "Answer the user's question in one short sentence using this data."
    )
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode())
        text = extract_output_text(result)
        if text:
            return text
        log(f"openai response had no message text: {json.dumps(result)[:500]}")
        return "Sorry, I couldn't reach the answering service right now."
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        log(f"openai request failed: {exc}")
        return "Sorry, I couldn't reach the answering service right now."


def resolve_window_start(now: int) -> int:
    """Real usage window if available, else state file, else schedule inference.

    The schedule is only an approximation: a 14:00 ping can land in a window
    that really runs 14:09-19:09, so ask Claude for the truth when we can.
    """
    try:
        usage = get_usage(now)
    except Exception as exc:  # noqa: BLE001 - must not break the poll loop
        log(f"usage lookup failed: {exc}")
        usage = None
    if usage and usage.get("session"):
        return derive_window_start(usage["session"]["resets_at"])

    state = load_state()
    window_start = state.get("window_start") or current_window_start(now)
    if window_start and now >= window_end(window_start):
        window_start = current_window_start(now)
    return window_start


def answer_question(env: dict, question: str) -> str:
    state = load_state()
    now = int(time.time())
    intent = match_intent(question)

    # Answered from the schedule alone, so skip the usage lookup's subprocess.
    if intent == "next_start":
        starts = next_start_times(now)
        if not starts:
            return "I couldn't work out the next session start time."
        return f"Next session window starts at {format_time(starts[0])} (in {humanize_delta(starts[0] - now)})."
    if intent == "next_next_start":
        starts = next_start_times(now)
        if len(starts) < 2:
            return "I couldn't work out the session start time after next."
        return f"The session window after next starts at {format_time(starts[1])} (in {humanize_delta(starts[1] - now)})."

    window_start = resolve_window_start(now)

    if intent == "usage":
        if not window_start:
            starts = next_start_times(now)
            nxt = f" Next one starts at {format_time(starts[0])}." if starts else ""
            return f"No session window is active right now.{nxt}"
        pct = usage_percent(window_start, now)
        return (
            f"Current window (opened {format_time(window_start)}) is {pct:.0f}% elapsed, "
            f"ends around {format_time(window_end(window_start))}."
        )
    if intent == "window_open":
        if not window_start:
            starts = next_start_times(now)
            nxt = f" Next one starts at {format_time(starts[0])}." if starts else ""
            return f"No session window is active right now.{nxt}"
        return f"Current window opened at {format_time(window_start)} ({humanize_delta(now - window_start)} ago)."
    if intent == "window_end":
        if not window_start:
            return "No session window is active right now."
        end = window_end(window_start)
        return f"Current window ends around {format_time(end)} ({humanize_delta(end - now)} left)."
    api_key = env.get("OPENAI_API_KEY")
    if not api_key:
        return "I don't recognize that question and no OPENAI_API_KEY is configured."
    model = env.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    return openai_answer(api_key, model, state, question, window_start)


def run() -> None:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    allowed_chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token or not allowed_chat_id:
        log("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not configured, exiting")
        return

    log("daemon started, polling for updates")
    offset = None
    consecutive_failures = 0
    while True:
        try:
            updates, failed, error_message = get_updates(token, offset)
            if failed:
                consecutive_failures += 1
                maybe_notify_poll_failure(token, allowed_chat_id, consecutive_failures, error_message or "unknown error")
                continue
            consecutive_failures = 0
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message", {})
                chat_id = str(message.get("chat", {}).get("id", ""))
                text = message.get("text", "")
                if not text or chat_id != str(allowed_chat_id):
                    continue
                log(f"question: {text}")
                reply = answer_question(env, text)
                log(f"reply: {reply}")
                send_message(token, chat_id, reply)
        except Exception as exc:  # noqa: BLE001 - defense in depth, must not crash poll loop
            log(f"unexpected error in poll loop: {exc}")


if __name__ == "__main__":
    run()
