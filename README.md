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
  - 04:02
  - 09:02
  - 14:02
  - 19:02

  launchd runs jobs with a minimal environment (bare `PATH`, no `USER`/
  `LOGNAME`), which isn't enough to find the `claude` CLI or for it to look
  up your login/auth. The template sets `PATH` (including
  `~/.local/bin`, where `claude` is commonly installed) and `USER`/
  `LOGNAME` explicitly via `EnvironmentVariables`, templated with
  `{{HOME_DIR}}`/`{{USER}}` placeholders that `install.sh` fills in.
- **`scripts/claude_session_ping.sh`** — the script launchd runs. It:
  1. Checks the current time is at one of the four targets above, or up to
     30 minutes after one (`CLAUDE_SESSION_PING_GRACE_MINUTES`) — see
     [Sleep](#sleep) below.
  2. Sends a keepalive ping (defaults to `claude -p "..."`).
  3. If it detects a usage-limit/blocked response, retries up to **4 times**,
     waiting 5 minutes between attempts (5 attempts total per window).
  4. Logs everything to `logs/claude-session-ping.log` in the project
     directory (override with `CLAUDE_SESSION_PING_LOG`).
  5. Asks Claude for the **real** usage window via `claude -p "/usage"` and
     reports the true start/end in notifications.

The schedule is only an approximation of the real window, which is why step 5
exists. A window starts when you first use Claude, not when the clock says
04:02 — so a 14:02 ping can land in a window that really runs 14:09–19:09. If
a window is already open, the ping is absorbed into it and **no new window
opens**; the notification says so explicitly instead of claiming success.
Reading `/usage` costs no quota and doesn't itself open a window. If the
lookup or parse fails, the script falls back to the scheduled assumption.

Nothing here needs an IDE, terminal, or Claude Code session to stay open —
once installed, `launchd` runs it independently as long as the Mac is on.

### Sleep

launchd does not wake the Mac for a `StartCalendarInterval` job. If the
machine is asleep at a target time, the job is deferred until the next wake
— in practice a macOS DarkWake minutes later. The script therefore accepts a
run up to 30 minutes late and still treats it as that target's window,
recording the target's label rather than the wake time. State prevents a
second late fire from pinging the same window twice, and a failed attempt is
still retried.

Past the grace window the run is skipped: the window has moved far enough
from the schedule that opening one would be more surprising than useful. To
keep windows exactly on time, wake the Mac just before each target:

```zsh
sudo pmset repeat wake MTWRFSU 04:00:00
```

Sleep also breaks the Telegram bot's long poll — each DarkWake surfaces the
dead socket as a read timeout. Those are logged but never alerted on, since
they say nothing about the bot's health.

## Install

```zsh
./install.sh
```

This generates `~/Library/LaunchAgents/com.claude-session-ping.plist` from
the template (substituting your actual home/project paths and username)
and loads it with `launchctl`. Re-run it any time the template changes.

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
cp .env.example .env
```

See that file for available options, including the optional Telegram
notifier/Q&A bot described below.

## Testing

Run the unit test suite (pure logic — schedule math, Q&A intent matching,
env-file parsing — no network calls, no real Claude/Telegram/OpenAI access):

```zsh
python3 -m unittest discover -s scripts/tests -t .
```

Simulate a keepalive trigger without waiting for the real time:

```zsh
CLAUDE_SESSION_PING_MOCK_TIME='09:02' ./scripts/claude_session_ping.sh
```

Or use `scripts/mock_session_ping.sh`, which does the same but substitutes a
fake `echo` command for the real Claude ping and prints the resulting log
from `logs/claude-session-ping.log`:

```zsh
./scripts/mock_session_ping.sh 09:02
```

## Uninstall

```zsh
launchctl unload ~/Library/LaunchAgents/com.claude-session-ping.plist
rm ~/Library/LaunchAgents/com.claude-session-ping.plist
```

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
4. Add these to `.env` in the project root (copy from
   `.env.example` if you haven't already):

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
- **Q&A**: the daemon answers these locally, with no OpenAI calls:
  - "what's my usage?" / "weekly limit?" → live session **and** weekly
    usage (percent used + reset times) from `claude -p "/usage"`; falls
    back to a clearly-labeled schedule estimate if the lookup fails
  - "when did this window open?"
  - "when does this window end?"
  - "what's the next session start time?" / "...next next...?"
  Anything else is sent to OpenAI (`gpt-5-nano` by default, override with
  `OPENAI_MODEL`) along with the current schedule state and live usage as
  context.

### Manual testing

Use either command from [Testing](#testing) above to trigger a mock
keepalive run — if `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are set, it'll
also send you a real Telegram notification.

Check the Q&A daemon is running and see its logs:

```zsh
launchctl list | grep claude-session-ping.telegram-bot
tail -f logs/claude-session-ping-telegram-bot.log
```

Then message your bot on Telegram directly and confirm it replies.

### Uninstall

```zsh
launchctl unload ~/Library/LaunchAgents/com.claude-session-ping.telegram-bot.plist
rm ~/Library/LaunchAgents/com.claude-session-ping.telegram-bot.plist
```

## Security

All secrets (Telegram bot token, chat id, OpenAI API key) live only in
the project's `.env` file, or wherever `CLAUDE_SESSION_PING_ENV_FILE` points
if you override it — both are gitignored and never committed. The
`logs/`, `.claude-session-ping/` (runtime logs and schedule state), and
`docs/` (design docs/specs/plans) directories are also gitignored —
`docs/` is kept purely as local, private scratch space and is never
pushed.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the project's release history.

## License

MIT — see [LICENSE](LICENSE).
