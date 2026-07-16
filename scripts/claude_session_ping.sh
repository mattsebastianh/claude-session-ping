#!/usr/bin/env zsh
set -euo pipefail

TARGETS=(0400 0900 1400 1900)
# launchd defers a missed StartCalendarInterval job until the machine wakes,
# so a 09:00 job can fire at 09:07 with the Mac having slept through 09:00.
# Accept a late run for that long, else the window is silently never opened.
GRACE_MINUTES="${CLAUDE_SESSION_PING_GRACE_MINUTES:-30}"
MAX_RETRIES="${CLAUDE_SESSION_PING_MAX_RETRIES:-4}"
RETRY_DELAY_SECONDS="${CLAUDE_SESSION_PING_RETRY_DELAY:-300}"
LIMIT_PATTERN='(usage limit|quota|blocked|rate limit|try again later)'
USAGE_LINK='https://claude.ai/new#settings/usage'
USAGE_CMD="${CLAUDE_SESSION_PING_USAGE_CMD:-python3 $(cd "$(dirname "$0")" && pwd)/claude_usage.py --shell}"
LOG_FILE="${CLAUDE_SESSION_PING_LOG:-$(cd "$(dirname "$0")/.." && pwd)/logs/claude-session-ping.log}"
STATE_FILE="${CLAUDE_SESSION_PING_STATE_FILE:-$(cd "$(dirname "$0")/.." && pwd)/.claude-session-ping/state.json}"
ENV_FILE="${CLAUDE_SESSION_PING_ENV_FILE:-$(cd "$(dirname "$0")/.." && pwd)/.env}"
MOCK_TIME="${CLAUDE_SESSION_PING_MOCK_TIME:-}"

if [[ -n "$MOCK_TIME" ]]; then
  CURRENT_TIME="${MOCK_TIME//:/}"
else
  CURRENT_TIME="$(date '+%H%M')"
fi

if [[ -f "$ENV_FILE" ]]; then
  source "$ENV_FILE"
fi

mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$(dirname "$STATE_FILE")"

hhmm_to_minutes() {
  print $(( 10#${1:0:2} * 60 + 10#${1:2:2} ))
}

# The target whose grace window contains now, or empty.
MATCHED_TARGET=""
CURRENT_MINUTES=$(hhmm_to_minutes "$CURRENT_TIME")
for target in "${TARGETS[@]}"; do
  delta=$(( CURRENT_MINUTES - $(hhmm_to_minutes "$target") ))
  if (( delta >= 0 && delta <= GRACE_MINUTES )); then
    MATCHED_TARGET="$target"
    break
  fi
done

if [[ -z "$MATCHED_TARGET" ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] skip (current time $CURRENT_TIME, no target within ${GRACE_MINUTES}m)" >>"$LOG_FILE"
  exit 0
fi

WINDOW_LABEL="${MATCHED_TARGET:0:2}:${MATCHED_TARGET:2:2}"

# A late run means launchd may fire this same target again after the next
# wake; without this guard that would burn a second ping on one window.
already_pinged_this_window() {
  [[ -f "$STATE_FILE" ]] || return 1
  python3 - "$STATE_FILE" "$WINDOW_LABEL" <<'PY'
import json, sys, time

WINDOW_SECONDS = 5 * 60 * 60
try:
    with open(sys.argv[1]) as fh:
        state = json.load(fh)
except (OSError, ValueError):
    sys.exit(1)
if state.get("window_label") != sys.argv[2]:
    sys.exit(1)
if state.get("status") != "success":
    sys.exit(1)  # a failed attempt should still be retried
if time.time() - state.get("updated_at", 0) > WINDOW_SECONDS:
    sys.exit(1)  # same label, but an earlier day's window
sys.exit(0)
PY
}

if already_pinged_this_window; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] skip (window $WINDOW_LABEL already pinged at $CURRENT_TIME)" >>"$LOG_FILE"
  exit 0
fi

if [[ "$CURRENT_TIME" != "$MATCHED_TARGET" ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] late run at $CURRENT_TIME for $WINDOW_LABEL target (launchd fired after wake)" >>"$LOG_FILE"
fi

write_state() {
  local ping_status="$1"
  local real_start="${2:-}"
  local start_epoch="${real_start:-$(date '+%s')}"
  cat >"$STATE_FILE" <<JSON
{"window_start": ${start_epoch}, "window_label": "${WINDOW_LABEL}", "status": "${ping_status}", "updated_at": $(date '+%s')}
JSON
}

notify_telegram() {
  local message="$1"
  if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
    return 0
  fi
  if ! curl -fsS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${message}" \
    --data-urlencode 'link_preview_options={"is_disabled":true}' \
    >>"$LOG_FILE" 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] telegram notify failed" >>"$LOG_FILE"
  fi
}

# Asks Claude for the real usage window. Populates USAGE_OK and, when
# USAGE_OK=1, SESSION_PCT/SESSION_RESETS_AT/WINDOW_START/WINDOW_IS_NEW and
# optionally WEEKLY_PCT/WEEKLY_WARN. Never fails the caller: the scheduled
# window is only ever an approximation of the real one, so a lookup failure
# just falls back to it.
load_usage() {
  USAGE_OK=0
  local output
  if ! output=$(eval "$USAGE_CMD" 2>/dev/null); then
    return 0
  fi
  local line
  for line in ${(f)output}; do
    case "$line" in
      USAGE_OK=*|SESSION_PCT=*|SESSION_RESETS_AT=*|WINDOW_START=*|WINDOW_IS_NEW=*|WEEKLY_PCT=*|WEEKLY_WARN=*)
        eval "$line"
        ;;
    esac
  done
  return 0
}

hhmm() {
  date -r "$1" '+%H:%M'
}

weekly_suffix() {
  if [[ "${WEEKLY_WARN:-0}" == "1" ]]; then
    printf ' Weekly limit at %s%%.' "${WEEKLY_PCT}"
  fi
}

success_message() {
  local body
  if [[ "${USAGE_OK:-0}" == "1" ]]; then
    if [[ "${WINDOW_IS_NEW:-0}" == "1" ]]; then
      body="✅ Claude session window opened at $(hhmm "$WINDOW_START") — active until $(hhmm "$SESSION_RESETS_AT")."
    else
      body="⚠️ No new window opened — existing window opened $(hhmm "$WINDOW_START"), runs until $(hhmm "$SESSION_RESETS_AT")."
    fi
    body="${body}$(weekly_suffix)"
  else
    body="✅ Claude session window opened at ${WINDOW_LABEL} — active until ~$(date -v+5H '+%H:%M')."
  fi
  printf '%s\n%s' "$body" "$USAGE_LINK"
}

failure_message() {
  local body="⚠️ Failed to open Claude session window at ${WINDOW_LABEL} after $((MAX_RETRIES + 1)) attempts. Check log."
  if [[ "${USAGE_OK:-0}" == "1" ]]; then
    body="${body} Current window runs until $(hhmm "$SESSION_RESETS_AT")."
    body="${body}$(weekly_suffix)"
  fi
  printf '%s\n%s' "$body" "$USAGE_LINK"
}

send_ping() {
  local message="$1"
  local output
  local exit_status

  if [[ -n "${CLAUDE_SESSION_PING_URL:-}" ]]; then
    output=$(curl -fsS -X POST "$CLAUDE_SESSION_PING_URL" \
      -H 'Content-Type: application/json' \
      --data "{\"message\":\"${message}\"}" 2>&1)
    exit_status=$?
  elif [[ -n "${CLAUDE_SESSION_PING_COMMAND:-}" ]]; then
    output=$(eval "$CLAUDE_SESSION_PING_COMMAND" 2>&1)
    exit_status=$?
  elif command -v claude >/dev/null 2>&1; then
    output=$(claude -p "$message" 2>&1)
    exit_status=$?
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] no Claude target configured" >>"$LOG_FILE"
    return 1
  fi

  echo "$output" >>"$LOG_FILE"

  if [[ $exit_status -ne 0 ]] || echo "$output" | grep -qiE "$LIMIT_PATTERN"; then
    return 1
  fi
  return 0
}

attempt=1
while true; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] sending Claude keepalive at $CURRENT_TIME (attempt $attempt/$((MAX_RETRIES + 1)))" >>"$LOG_FILE"
  MESSAGE="keepalive ping $(date '+%Y-%m-%d %H:%M:%S')"

  if send_ping "$MESSAGE"; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] sent successfully (attempt $attempt)" >>"$LOG_FILE"
    load_usage
    if [[ "${USAGE_OK:-0}" == "1" ]]; then
      write_state "success" "$WINDOW_START"
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] real window $(hhmm "$WINDOW_START")-$(hhmm "$SESSION_RESETS_AT") (new=${WINDOW_IS_NEW})" >>"$LOG_FILE"
    else
      write_state "success"
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] usage lookup unavailable, using scheduled window" >>"$LOG_FILE"
    fi
    notify_telegram "$(success_message)"
    exit 0
  fi

  if [[ $attempt -gt $MAX_RETRIES ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] attempt $attempt failed, max retries ($MAX_RETRIES) reached, giving up for this window" >>"$LOG_FILE"
    write_state "failed"
    load_usage
    notify_telegram "$(failure_message)"
    exit 1
  fi

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] attempt $attempt failed or hit credit limit, retrying in ${RETRY_DELAY_SECONDS}s" >>"$LOG_FILE"
  sleep "$RETRY_DELAY_SECONDS"
  attempt=$((attempt + 1))
done
