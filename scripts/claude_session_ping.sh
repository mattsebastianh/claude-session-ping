#!/usr/bin/env zsh
set -euo pipefail

TARGETS=(0400 0900 1400 1900)
MAX_RETRIES=4
RETRY_DELAY_SECONDS=300
LIMIT_PATTERN='(usage limit|quota|blocked|rate limit|try again later)'
LOG_FILE="${CLAUDE_SESSION_PING_LOG:-$HOME/Library/Logs/claude-session-ping.log}"
ENV_FILE="${CLAUDE_SESSION_PING_ENV_FILE:-$HOME/.claude-session-ping.env}"
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

if [[ ! " ${TARGETS[*]} " =~ " ${CURRENT_TIME} " ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] skip (current time $CURRENT_TIME)" >>"$LOG_FILE"
  exit 0
fi

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
    exit 0
  fi

  if [[ $attempt -gt $MAX_RETRIES ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] attempt $attempt failed, max retries ($MAX_RETRIES) reached, giving up for this window" >>"$LOG_FILE"
    exit 1
  fi

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] attempt $attempt failed or hit credit limit, retrying in ${RETRY_DELAY_SECONDS}s" >>"$LOG_FILE"
  sleep "$RETRY_DELAY_SECONDS"
  attempt=$((attempt + 1))
done
