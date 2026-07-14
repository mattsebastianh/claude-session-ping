#!/usr/bin/env zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PLIST_TEMPLATE="$ROOT/launchd/com.claude-session-ping.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.claude-session-ping.plist"

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|{{PROJECT_DIR}}|$ROOT|g" -e "s|{{HOME_DIR}}|$HOME|g" "$PLIST_TEMPLATE" >"$PLIST_DEST"
chmod 644 "$PLIST_DEST"
chmod +x "$ROOT/scripts/claude_session_ping.sh"

launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

echo "Installed and loaded launch agent: $PLIST_DEST"
echo "Use 'launchctl list | grep claude-session-ping' to confirm"
