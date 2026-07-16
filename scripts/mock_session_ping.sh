#!/usr/bin/env zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MOCK_TIME="${1:-09:02}"
LOG_FILE="$ROOT/logs/claude-session-ping.log"

mkdir -p "$ROOT/logs"
rm -f "$LOG_FILE"

CLAUDE_SESSION_PING_MOCK_TIME="$MOCK_TIME" \
CLAUDE_SESSION_PING_COMMAND='echo mock-ping' \
./scripts/claude_session_ping.sh

echo "Mock session ping completed. Log file: $LOG_FILE"
echo '--- latest log ---'
tail -n 20 "$LOG_FILE"
