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
from telegram_qa_lib import (  # noqa: E402
    format_time,
    match_intent,
    next_start_times,
    parse_env_text,
    usage_percent,
    window_end,
)

ENV_FILE = Path(os.environ.get("CLAUDE_SESSION_PING_ENV_FILE", str(Path.home() / ".claude-session-ping.env")))
STATE_FILE = Path(os.environ.get("CLAUDE_SESSION_PING_STATE_FILE", str(Path.home() / ".claude-session-ping" / "state.json")))
LOG_FILE = Path(os.environ.get(
    "CLAUDE_SESSION_PING_TELEGRAM_BOT_LOG",
    str(Path.home() / "Library" / "Logs" / "claude-session-ping-telegram-bot.log"),
))

POLL_TIMEOUT_SECONDS = 30
DEFAULT_OPENAI_MODEL = "gpt-5-nano"


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
    except urllib.error.URLError as exc:
        log(f"sendMessage failed: {exc}")


def get_updates(token: str, offset: int | None) -> list[dict]:
    params: dict = {"timeout": POLL_TIMEOUT_SECONDS}
    if offset is not None:
        params["offset"] = offset
    try:
        result = telegram_request(token, "getUpdates", params, timeout=POLL_TIMEOUT_SECONDS + 10)
    except urllib.error.URLError as exc:
        log(f"getUpdates failed: {exc}")
        return []
    return result.get("result", [])


def openai_answer(api_key: str, model: str, state: dict, question: str) -> str:
    now = int(time.time())
    starts = next_start_times(now)
    system_prompt = (
        "You are a terse status bot for a Claude Code keepalive scheduler. "
        "Daily windows open at 04:00, 09:00, 14:00, 19:00 and each stays active for 5 hours. "
        f"Current window: label={state.get('window_label')}, "
        f"opened_at={format_time(state['window_start']) if state.get('window_start') else 'unknown'}, "
        f"status={state.get('status')}. "
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
        return result["output"][0]["content"][0]["text"].strip()
    except (urllib.error.URLError, KeyError, IndexError) as exc:
        log(f"openai request failed: {exc}")
        return "Sorry, I couldn't reach the answering service right now."


def answer_question(env: dict, question: str) -> str:
    state = load_state()
    now = int(time.time())
    intent = match_intent(question)

    if intent == "usage":
        pct = usage_percent(state.get("window_start", 0), now)
        return f"Current window usage: {pct:.0f}% elapsed."
    if intent == "window_end":
        if not state.get("window_start"):
            return "No window is currently tracked yet."
        return f"Current window ends around {format_time(window_end(state['window_start']))}."
    if intent == "next_start":
        starts = next_start_times(now)
        return f"Next session start time: {format_time(starts[0])}." if starts else "Unknown."
    if intent == "next_next_start":
        starts = next_start_times(now)
        return f"Next-next session start time: {format_time(starts[1])}." if len(starts) > 1 else "Unknown."

    api_key = env.get("OPENAI_API_KEY")
    if not api_key:
        return "I don't recognize that question and no OPENAI_API_KEY is configured."
    model = env.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    return openai_answer(api_key, model, state, question)


def run() -> None:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    allowed_chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token or not allowed_chat_id:
        log("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not configured, exiting")
        return

    log("daemon started, polling for updates")
    offset = None
    while True:
        updates = get_updates(token, offset)
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


if __name__ == "__main__":
    run()
