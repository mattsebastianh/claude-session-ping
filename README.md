# claude-session-ping

A macOS `launchd` agent that pings Claude Code on a fixed daily schedule to
activate/keep alive the 5-hour session usage window — no LLM required to
decide *when* to fire, only plain system scheduling and shell code.

## How it works

- **`launchd/com.claude-session-ping.plist`** — a launch agent template.
  macOS's own scheduler (`StartCalendarInterval`) fires it daily at:
  - 04:00
  - 09:00
  - 14:00
  - 19:00
- **`scripts/claude_session_ping.sh`** — the script launchd runs. It:
  1. Checks the current time is one of the four windows above (safety net).
  2. Sends a keepalive ping (defaults to `claude -p "..."`).
  3. If it detects a usage-limit/blocked response, retries up to **4 times**,
     waiting 5 minutes between attempts (5 attempts total per window).
  4. Logs everything to `~/Library/Logs/claude-session-ping.log`.

Nothing here needs an IDE, terminal, or Claude Code session to stay open —
once installed, `launchd` runs it independently as long as the Mac is on.

## Install

```zsh
./install.sh
```

This generates `~/Library/LaunchAgents/com.claude-session-ping.plist` from
the template (substituting your actual home/project paths) and loads it
with `launchctl`.

Confirm it's loaded:

```zsh
launchctl list | grep claude-session-ping
```

## Configuration (optional)

The script works out of the box with no configuration — it just calls
`claude -p` directly. To customize behavior (send pings to a webhook
instead, run a custom command, or fake the time for testing), copy the
example env file:

```zsh
cp claude-session-ping.env.example ~/.claude-session-ping.env
```

See that file for available options.

## Testing

Simulate a trigger without waiting for the real time:

```zsh
CLAUDE_SESSION_PING_MOCK_TIME='09:00' ./scripts/claude_session_ping.sh
```

## Uninstall

```zsh
launchctl unload ~/Library/LaunchAgents/com.claude-session-ping.plist
rm ~/Library/LaunchAgents/com.claude-session-ping.plist
```

## License

MIT — see [LICENSE](LICENSE).
