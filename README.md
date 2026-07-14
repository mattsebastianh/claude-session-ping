# claude-session-ping

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://www.apple.com/macos/)
[![Shell: zsh](https://img.shields.io/badge/shell-zsh-89e051.svg)](scripts/claude_session_ping.sh)

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

See that file for available options, including the optional Telegram
notifier/Q&A bot described below.

## Telegram notifications + Q&A bot (optional)

Get a Telegram message every time a keepalive window opens (or fails to
open after all retries), and ask a bot ad-hoc questions like "what's my
usage %?", "when does this window end?", or "what's the next session
start time?". Fully optional — leave the variables below unset and
nothing about the existing behavior changes.

### Setup

1. **Create a bot**: message **@BotFather** on Telegram, run `/newbot`,
   pick a name and a username ending in `bot`. It replies with a token
   like `123456789:AAExample...` — treat it as a secret.
2. **Get your chat id**: send your new bot any message (e.g. "hi"), then
   visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a
   browser and read the numeric `"chat":{"id": ...}` value from the
   response.
3. **Get an OpenAI API key** (optional, only used as a fallback for
   questions the bot doesn't recognize) from your OpenAI account.
4. Add these to `~/.claude-session-ping.env` (copy from
   `claude-session-ping.env.example` if you haven't already):

   ```
   TELEGRAM_BOT_TOKEN='123456789:AAExampleTokenReplaceMe'
   TELEGRAM_CHAT_ID='987654321'
   OPENAI_API_KEY='sk-exampleReplaceMe'
   ```
5. Re-run `./install.sh`. It detects `TELEGRAM_BOT_TOKEN` in your env
   file and additionally installs `com.claude-session-ping.telegram-bot`,
   a long-running launchd job that polls Telegram for questions.

### What it does

- **Notifications**: `claude_session_ping.sh` posts a message on every
  attempt's outcome — success (window opened) or failure (all retries
  exhausted).
- **Q&A**: the daemon answers these locally, from a shared state file,
  with no API calls:
  - "what's my usage %?" → time elapsed in the current 5-hour window
  - "when did this window open?"
  - "when does this window end?"
  - "what's the next session start time?" / "...next next...?"
  Anything else is sent to OpenAI (`gpt-5-nano` by default, override with
  `OPENAI_MODEL`) along with the current schedule state as context.

### Manual testing

Trigger a one-off notify without waiting for a real window:

```zsh
CLAUDE_SESSION_PING_MOCK_TIME='09:00' ./scripts/claude_session_ping.sh
```

Check the daemon is running and see its logs:

```zsh
launchctl list | grep claude-session-ping.telegram-bot
tail -f ~/Library/Logs/claude-session-ping-telegram-bot.log
```

Then message your bot on Telegram directly and confirm it replies.

### Uninstall

```zsh
launchctl unload ~/Library/LaunchAgents/com.claude-session-ping.telegram-bot.plist
rm ~/Library/LaunchAgents/com.claude-session-ping.telegram-bot.plist
```

## Security

All secrets (Telegram bot token, chat id, OpenAI API key) live only in
`~/.claude-session-ping.env` / `.env` — both are gitignored and never
committed. When writing design docs or specs in `docs/`, use placeholder
values (e.g. `sk-exampleReplaceMe`) rather than real credentials; nothing
in `docs/` is gitignored, since design history is meant to be tracked.

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
