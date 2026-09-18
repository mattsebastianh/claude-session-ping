#!/usr/bin/env python3
"""Long-polling Telegram Q&A daemon for claude-session-ping.

Answers questions about the current keepalive schedule using the shared
state file written by scripts/claude_session_ping.sh, falling back to an
OpenRouter chat completion for anything it doesn't recognize.

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
    BOT_COMMANDS,
    command_intent,
    counts_toward_outage,
    current_window_start,
    extract_chat_completion_text,
    next_failure_count,
    format_status_reply,
    format_time,
    format_usage_reply,
    humanize_delta,
    match_intent,
    describe_next_start,
    describe_triggers,
    next_start_times,
    parse_env_text,
    scheduled_triggers,
    usage_percent,
    usage_prompt_line,
    window_end,
)
from usage_lib import derive_window_start  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = Path(os.environ.get("CLAUDE_SESSION_PING_ENV_FILE", str(ROOT / ".env")))
STATE_FILE = Path(os.environ.get("CLAUDE_SESSION_PING_STATE_FILE", str(ROOT / ".claude-session-ping" / "state.json")))
AGENT_DIR = Path(os.environ.get(
    "CLAUDE_SESSION_PING_BACKUP_DIR", str(Path.home() / "Library" / "LaunchAgents")))
LOG_FILE = Path(os.environ.get(
    "CLAUDE_SESSION_PING_TELEGRAM_BOT_LOG",
    str(Path(__file__).resolve().parents[1] / "logs" / "claude-session-ping-telegram-bot.log"),
))

POLL_TIMEOUT_SECONDS = 30
# gpt-oss-20b is OpenRouter's cheapest gpt-oss variant (gpt-oss-120b costs more).
DEFAULT_OPENROUTER_MODEL = "openai/gpt-oss-20b"
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


def register_commands(token: str) -> None:
    """Publish the slash-command menu so it appears in Telegram's UI.

    Best-effort: a failure here costs the menu, not the bot, so it must never
    stop the poll loop from starting.
    """
    commands = [{"command": name, "description": desc} for name, desc in BOT_COMMANDS]
    try:
        telegram_request(token, "setMyCommands", {"commands": json.dumps(commands)}, timeout=10)
        log(f"registered {len(commands)} bot commands")
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        log(f"setMyCommands failed: {exc}")


def maybe_notify_poll_failure(token: str, chat_id: str, failure_count: int, exc: str) -> None:
    if failure_count != MAX_GETUPDATES_FAILURES_BEFORE_ALERT:
        return
    warning = (
        f"Telegram polling has failed {failure_count} times in a row; "
        f"last error: {exc}. I will notify you if it continues."
    )
    log(warning)
    send_message(token, chat_id, warning)


def get_updates(token: str, offset: int | None) -> tuple[list[dict], str, str | None]:
    """Poll for updates. Returns (updates, status, error_message).

    status is "ok", "transient" (a routine long-poll/sleep timeout), or
    "error" (worth counting toward the outage alert).
    """
    params: dict = {"timeout": POLL_TIMEOUT_SECONDS}
    if offset is not None:
        params["offset"] = offset
    try:
        result = telegram_request(token, "getUpdates", params, timeout=POLL_TIMEOUT_SECONDS + 10)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        is_outage = counts_toward_outage(exc)
        # Transient timeouts are the macOS DarkWake case: every sleep freezes
        # the long-poll socket and wakes to one read timeout. Logging each one
        # is pure noise (~1-3/hour), so only log outage-worthy failures.
        if is_outage:
            log(f"getUpdates failed: {exc}")
        time.sleep(5)
        return [], "error" if is_outage else "transient", str(exc)
    return result.get("result", []), "ok", None


def openrouter_answer(api_key: str, model: str, state: dict, question: str, window_start: int = 0, usage: dict | None = None) -> str:
    now = int(time.time())
    triggers = scheduled_triggers(AGENT_DIR)
    starts = next_start_times(now, [hhmm for hhmm, _ in triggers])
    if window_start:
        window_desc = (
            f"opened_at={format_time(window_start)}, "
            f"ends_at={format_time(window_end(window_start))}, "
            f"elapsed={usage_percent(window_start, now):.0f}%"
        )
    else:
        window_desc = "none active"
    system_prompt = (
        "You are a status bot for a Claude Code keepalive scheduler. "
        f"Scheduled pings: {describe_triggers(triggers)}. Each window stays active for 5 hours. "
        f"Current window: {window_desc}, "
        f"last_ping_status={state.get('status')}. "
        f"Next start: {format_time(starts[0]) if starts else 'unknown'}. "
        f"Next next start: {format_time(starts[1]) if len(starts) > 1 else 'unknown'}. "
        f"{usage_prompt_line(usage)}"
        "Answer the user's question using only the data above. "
        "If the answer has more than one fact, format it as short labeled "
        "lines (one fact per line, each starting with a relevant emoji) "
        "instead of a single run-on sentence — for example:\n"
        "🪟 Window: 07:02–12:02 (48% elapsed)\n"
        "✅ Last ping: success\n"
        "⏭️ Next start: 12:02\n"
        "⏭️ Then: 17:02\n"
        "📊 Session: 6% used — resets 12:02\n"
        "📅 Weekly: 3% used — resets Sun 00:00\n"
        "If the question asks for \"info\", \"status\", \"summary\", or is "
        "otherwise open-ended, output ALL six lines — \"session info\" is a "
        "request for the whole picture, not just the session line. Reply "
        "with a single line only when the question names one specific fact "
        "(e.g. \"when does the window end?\")."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode())
        text = extract_chat_completion_text(result)
        if text:
            return text
        log(f"openrouter response had no message text: {json.dumps(result)[:500]}")
        return "Sorry, I couldn't reach the answering service right now."
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        log(f"openrouter request failed: {exc}")
        return "Sorry, I couldn't reach the answering service right now."


def fetch_usage_and_window(now: int) -> tuple[dict | None, int]:
    """Live usage dict plus window start; (None, fallback) when lookup fails.

    The schedule is only an approximation: a 17:02 ping can land in a window
    that really runs 14:09-19:09, so ask Claude for the truth when we can.
    """
    try:
        usage = get_usage(now)
    except Exception as exc:  # noqa: BLE001 - must not break the poll loop
        log(f"usage lookup failed: {exc}")
        usage = None
    if usage and usage.get("session"):
        return usage, derive_window_start(usage["session"]["resets_at"])

    state = load_state()
    window_start = state.get("window_start") or current_window_start(now)
    if window_start and now >= window_end(window_start):
        window_start = current_window_start(now)
    return usage, window_start


def answer_question(env: dict, question: str) -> str:
    state = load_state()
    now = int(time.time())
    # Read the schedule per question rather than caching it: the daemon is
    # long-running, and the backup agent it must report on is created and
    # reaped by ping runs while this process stays up.
    triggers = scheduled_triggers(AGENT_DIR)
    targets = [hhmm for hhmm, _ in triggers]
    # A slash command is an explicit intent, so it bypasses keyword matching
    # (and never reaches the LLM fallback).
    intent = command_intent(question) or match_intent(question)

    # Answered from the schedule alone, so skip the usage lookup's subprocess.
    if intent == "next_start":
        starts = next_start_times(now, targets)
        if not starts:
            return "I couldn't work out the next session start time."
        return describe_next_start(triggers, starts[0], now)
    if intent == "next_next_start":
        starts = next_start_times(now, targets)
        if len(starts) < 2:
            return "I couldn't work out the session start time after next."
        return f"The ping after next is at {format_time(starts[1])} (in {humanize_delta(starts[1] - now)})."

    usage, window_start = fetch_usage_and_window(now)

    if intent == "status":
        return format_status_reply(usage, window_start, str(state.get("status", "unknown")), now, targets)
    if intent == "usage":
        if usage:
            return format_usage_reply(usage, now)
        if not window_start:
            starts = next_start_times(now, targets)
            nxt = f" Next one starts at {format_time(starts[0])}." if starts else ""
            return f"No session window is active right now.{nxt}"
        pct = usage_percent(window_start, now)
        return (
            "⚠️ Couldn't fetch live usage. Estimate from schedule:\n"
            f"📊 Session window ~{pct:.0f}% elapsed — ends around {format_time(window_end(window_start))}"
        )
    if intent == "window_open":
        if not window_start:
            starts = next_start_times(now, targets)
            nxt = f" Next one starts at {format_time(starts[0])}." if starts else ""
            return f"No session window is active right now.{nxt}"
        return f"Current window opened at {format_time(window_start)} ({humanize_delta(now - window_start)} ago)."
    if intent == "window_end":
        if not window_start:
            return "No session window is active right now."
        end = window_end(window_start)
        return f"Current window ends around {format_time(end)} ({humanize_delta(end - now)} left)."
    api_key = env.get("OPENROUTER_API_KEY")
    if not api_key:
        return "I don't recognize that question and no OPENROUTER_API_KEY is configured."
    model = env.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
    return openrouter_answer(api_key, model, state, question, window_start, usage)


def run() -> None:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    allowed_chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token or not allowed_chat_id:
        log("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not configured, exiting")
        return

    register_commands(token)
    log("daemon started, polling for updates")
    offset = None
    consecutive_failures = 0
    while True:
        try:
            updates, status, error_message = get_updates(token, offset)
            consecutive_failures = next_failure_count(consecutive_failures, status)
            if status == "error":
                maybe_notify_poll_failure(token, allowed_chat_id, consecutive_failures, error_message or "unknown error")
            if status != "ok":
                continue
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
