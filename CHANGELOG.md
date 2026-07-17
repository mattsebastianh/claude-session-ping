# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Backup ping: when a scheduled ping finds an existing window still open, a
  one-shot launchd job is scheduled to re-open coverage just after that window
  ends (window end + `CLAUDE_SESSION_PING_BACKUP_BUFFER`, default 120s). It
  re-chains until a fresh window opens and is suppressed past
  `CLAUDE_SESSION_PING_BACKUP_CUTOFF` (default 23:02) to protect the 04:02 target.

### Fixed
- Transient `getUpdates` read timeouts (the routine macOS DarkWake case — every
  sleep freezes the long-poll socket and wakes to one timeout) are no longer
  logged. They were already excluded from the outage alert, but each one still
  wrote a log line: ~1-3/hour, 134 lines in one 2.5-day stretch, drowning out
  genuine errors. Outage-worthy failures (DNS, network-unreachable) still log
  and still count toward the alert.

## [2.1.0] - 2026-07-16

### Changed
- Schedule shifted two minutes later, to 04:02 / 09:02 / 14:02 / 19:02, so a
  ping lands clear of the previous window's exact expiry rather than racing
  it. Applies to the launch agent, the ping script, and the bot's schedule
  answers.

## [2.0.1] - 2026-07-16

Sleep resilience: on a Mac that sleeps, windows were silently missed, the
bot answered from the schedule instead of live usage, and the poll alert
cried wolf.

### Fixed
- Keepalive windows are no longer silently missed when the Mac is asleep at
  a target time. launchd defers a missed `StartCalendarInterval` job until
  the machine wakes, so a 09:00 job fired at 09:07 and the exact-time guard
  skipped it — the window never opened. A run is now accepted up to
  `CLAUDE_SESSION_PING_GRACE_MINUTES` (default 30) after a target, guarded
  by state so a second late fire can't double-ping one window.
- The Telegram daemon can find `claude` again: its launch agent had no
  `EnvironmentVariables` block, so launchd gave it a default `PATH` without
  `~/.local/bin`. Every `/usage` lookup failed silently and the bot answered
  from the schedule instead of the real window (reporting a 09:00–14:00
  window when the real one was 09:59–14:59). The v2.0.0 `PATH` fix had
  reached the ping agent only.
- Long-poll read timeouts no longer trigger the "polling has failed" alert.
  Each macOS DarkWake kills the pending `getUpdates` socket, and with the
  Mac sleeping straight back no successful poll reset the counter — 92
  timeouts produced 12 false alarms in a single night. Timeouts are now
  logged but treated as transient; genuine errors still alert.

## [2.0.0] - 2026-07-16

Telegram notifier + Q&A bot, plus real usage-window reporting, built on top
of the v1.0.0 keepalive core.

### Added
- The Q&A bot answers usage questions ("what's my usage?", "weekly
  limit?") with live data from `claude -p "/usage"` — percent used and
  reset time for both the session window and the weekly limit — instead
  of reporting window time elapsed as if it were usage. Falls back to a
  clearly-labeled schedule estimate when the lookup fails.
- Free-form (OpenAI-answered) questions include live usage as context.
- Real usage-window reporting: notifications now show the true window start
  and end, parsed from `claude -p "/usage"`, instead of assuming the window
  equals the scheduled time plus five hours. The two drift apart in practice
  — a 14:00 ping can land in a window that really runs 14:09–19:09.
- Notifications distinguish "this ping opened a new window" from "the ping
  landed inside a window that was already open", which previously reported
  success and invented a window end five hours from the ping.
- Weekly limit warning appended to notifications at >= 80% used.
- Usage link (`https://claude.ai/new#settings/usage`) on scheduled
  notifications.
- `scripts/usage_lib.py` (pure parser) and `scripts/claude_usage.py` (IO
  wrapper), with unit tests covering the observed output variants. Reading
  `/usage` makes no API call, so it neither consumes quota nor opens a
  window.
- The Q&A bot answers window questions from the real window when available,
  falling back to the state file and then the schedule.
- `CLAUDE_SESSION_PING_MAX_RETRIES` and `CLAUDE_SESSION_PING_RETRY_DELAY`
  env overrides, so the retry path is testable without waiting 25 minutes.
- Telegram notifications on every keepalive attempt's outcome (window opened,
  or all retries exhausted).
- Telegram Q&A daemon (`scripts/telegram_qa_daemon.py`) that answers schedule
  questions locally from a shared state file — usage %, window open/end time,
  next/next-next session start — with an OpenAI fallback for anything else.
- `window_open` Q&A intent ("when did this window open?").
- Answers infer the active window from the fixed schedule when the state
  file is missing or stale, instead of only trusting the last-written state.
- Own launchd job (`com.claude-session-ping.telegram-bot`) installed by
  `install.sh` when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set.
- `.env.example` documents the Telegram/OpenAI env vars.
- Design spec and implementation plan for the notifier/Q&A bot under `docs/`.
- `scripts/mock_session_ping.sh` to run a deterministic mock keepalive ping and
  verify log output lands in the project log folder.

### Fixed
- Telegram notifications no longer render a link preview card for the
  usage URL.
- `match_intent` no longer false-positives on substrings like "weekend" or
  "recover" matching `window_end`-ish keywords.
- Network errors from the OpenAI fallback no longer crash the poll loop.
- `install.sh` requires a non-empty `TELEGRAM_CHAT_ID`, not just a token,
  before installing the Telegram daemon.
- `openai_answer` scans the Responses API output for the first message item
  instead of assuming it's first, so reasoning items before it no longer
  break answers.
- `parse_env_text` supports `export KEY=value` lines and unquoted inline
  `# comments`, matching how the env file is actually sourced by zsh.
- Both the scheduled ping and Telegram daemon now default their logs to the
  project-local `logs/` directory instead of the home `~/Library/Logs` folder.
- The project now defaults to a repo-local `.env` file for configuration,
  while still honoring `CLAUDE_SESSION_PING_ENV_FILE` when explicitly set.
- State file (`state.json`) also now defaults to the project-local
  `.claude-session-ping/` directory instead of the home directory.
- The launch agent now sets `PATH` and `USER`/`LOGNAME` explicitly (via new
  `{{HOME_DIR}}`/`{{USER}}` template placeholders filled in by `install.sh`).
  launchd's default job environment is minimal enough that `claude` couldn't
  be found on `PATH` and, once found, reported "Not logged in" for lack of
  `USER` — both caused every scheduled keepalive attempt to fail silently
  until the next window.

## [1.0.0] - 2026-07-13

Initial release: a `launchd`-based keepalive ping, no LLM required to decide
*when* to fire.

### Added
- `launchd/com.claude-session-ping.plist` launch agent template firing daily
  at 04:00, 09:00, 14:00, and 19:00.
- `scripts/claude_session_ping.sh`: checks the current time against the
  schedule, sends the keepalive ping, and retries up to 4 times (5 attempts
  total per window) on a usage-limit/blocked response.
- `install.sh` to install the launch agent.
- MIT license and initial README.

[Unreleased]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.0.1...v2.1.0
[2.0.1]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/mattsebastianh/claude-session-ping/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/mattsebastianh/claude-session-ping/releases/tag/v1.0.0
