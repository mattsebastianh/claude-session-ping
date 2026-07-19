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
     65 minutes after one (`CLAUDE_SESSION_PING_GRACE_MINUTES`) — see
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

Telling those two cases apart is not just clock proximity: Anthropic anchors
the reported window start to a coarse boundary that can precede the opening
ping by several minutes (a 04:04 ping can produce a window reported as
04:00–09:00). A window counts as new when it started within the last 40
minutes — covering that anchoring plus lookup latency — and
not before the previous window's recorded end (`resets_at` in `state.json`).

Nothing here needs an IDE, terminal, or Claude Code session to stay open —
once installed, `launchd` runs it independently as long as the Mac is on.

### Sleep

launchd does not wake the Mac for a `StartCalendarInterval` job. If the
machine is asleep at a target time, the job is deferred until the next wake
— a macOS DarkWake. The script therefore accepts a run up to 65 minutes late
and still treats it as that target's window, recording the target's label
rather than the wake time. State prevents a second late fire from pinging
the same window twice, and a failed attempt is still retried.

65 minutes is sized to a full sleep cycle, not to a typical wake delay. An
idle Mac on AC cycles roughly hourly maintenance sleeps, so a target missed
by seconds is not retried for nearly an hour: on 2026-07-19 the machine
slept at 08:59:11 — 11 seconds before the 09:02 target — and did not
DarkWake until 09:59:26. A 30-minute grace dropped that day's 04:02 and
09:02 windows entirely.

Past the grace window the run is skipped: the window has moved far enough
from the schedule that opening one would be more surprising than useful.

A late run still opens a real window, just a late one, which shifts every
downstream window (the [backup ping](#backup-ping) exists to absorb exactly
that drift). To keep the first window of the day exactly on time, schedule a
daily wake just before the 04:02 target:

```zsh
sudo pmset repeat wake MTWRFSU 04:00:00
```

Note that `pmset repeat` supports only **one** repeating wake event, so this
covers 04:02 alone; the remaining three targets still rely on the grace
window. Confirm it took with `pmset -g sched`.

Sleep also breaks the Telegram bot's long poll — each DarkWake surfaces the
dead socket as a read timeout. Those are silently retried and never alerted
on, since they say nothing about the bot's health; only genuine errors
(DNS failures, network unreachable) are logged and count toward the alert.

### Backup ping

The fixed 04:02/09:02/14:02/19:02 schedule is only ever an approximation —
real windows drift away from it (see [How it works](#how-it-works) above), so
a later scheduled ping can land inside a window that's still open rather than
opening a fresh one. When that happens, the ping is absorbed into the
existing window and no new coverage is created, which would otherwise leave
a gap once that window actually ends.

To close that gap, a no-new-window ping schedules a one-shot `launchd` job to
fire shortly after the real window ends — window end +
`CLAUDE_SESSION_PING_BACKUP_BUFFER` seconds (default 120) — and re-open
coverage automatically. If that backup ping also lands in an already-open
window (e.g. you used Claude again in the meantime), it re-chains another
backup the same way, repeating until a fresh window actually opens. Each
backup fire installs its own one-shot launch agent, labeled
`com.claude-session-ping.backup-HHMM.plist` in `~/Library/LaunchAgents`.

Backups are suppressed once the fire time would fall past
`CLAUDE_SESSION_PING_BACKUP_CUTOFF` (default 23:02) local time — without that
cutoff, a very late-running chain could push a backup into the early morning
and collide with, or crowd out, the 04:02 target.

They are also suppressed when a regular scheduled target falls between the
window's end and the backup's fire time: that target reopens coverage by
itself, and the backup would fire in the same instant (a window ending 09:00
puts the backup at 09:02 — exactly the 09:02 target), double-pinging and
sending contradictory notifications.

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
