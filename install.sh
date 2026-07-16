#!/usr/bin/env zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PLIST_TEMPLATE="$ROOT/launchd/com.claude-session-ping.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.claude-session-ping.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"
sed -e "s|{{PROJECT_DIR}}|$ROOT|g" -e "s|{{HOME_DIR}}|$HOME|g" -e "s|{{USER}}|$USER|g" "$PLIST_TEMPLATE" >"$PLIST_DEST"
chmod 644 "$PLIST_DEST"
chmod +x "$ROOT/scripts/claude_session_ping.sh"

launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

echo "Installed and loaded launch agent: $PLIST_DEST"
echo "Use 'launchctl list | grep claude-session-ping' to confirm"

ENV_FILE="${CLAUDE_SESSION_PING_ENV_FILE:-$ROOT/.env}"
if [[ -f "$ENV_FILE" ]] && grep -qE '^TELEGRAM_BOT_TOKEN=.+' "$ENV_FILE" && grep -qE '^TELEGRAM_CHAT_ID=.+' "$ENV_FILE"; then
  BOT_PLIST_TEMPLATE="$ROOT/launchd/com.claude-session-ping.telegram-bot.plist"
  BOT_PLIST_DEST="$HOME/Library/LaunchAgents/com.claude-session-ping.telegram-bot.plist"
  sed -e "s|{{PROJECT_DIR}}|$ROOT|g" -e "s|{{HOME_DIR}}|$HOME|g" -e "s|{{USER}}|$USER|g" "$BOT_PLIST_TEMPLATE" >"$BOT_PLIST_DEST"
  chmod 644 "$BOT_PLIST_DEST"
  chmod +x "$ROOT/scripts/telegram_qa_daemon.py"

  launchctl unload "$BOT_PLIST_DEST" 2>/dev/null || true
  launchctl load "$BOT_PLIST_DEST"

  echo "Installed and loaded Telegram Q&A daemon: $BOT_PLIST_DEST"
  echo "Use 'launchctl list | grep claude-session-ping.telegram-bot' to confirm"
else
  echo "TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID not set in $ENV_FILE, skipping Telegram Q&A daemon install."
  echo "Configure them and re-run ./install.sh to enable the Telegram bot."
fi
