# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [2.2.2] - 2026-07-19

### Changed
- Post-wake grace window (`CLAUDE_SESSION_PING_GRACE_MINUTES`) default raised
  30 → **65 minutes** — an idle Mac's hourly maintenance-sleep cycle could
  outlast the old grace and silently drop whole windows.

### Fixed
- New-window detection no longer misreports a fresh window as "No new window
  opened": new = started within the last 40 minutes and not before the
  previous window's recorded end (`resets_at`, newly tracked in `state.json`).
- No backup ping is scheduled when a regular target already covers the
  reopening (both could fire at the same instant, double-pinging with
  contradictory notifications).
- Backup cleanup no longer SIGTERMs itself midway: files and log lines settle
  before launchd drops the jobs, own job last.

## [2.2.1] - 2026-07-17

### Changed
- `.gitignore` grouped into labeled sections; stale, never-referenced
  `.claude-session-ping.env` entry dropped.

## [2.2.0] - 2026-07-17

### Added
- Backup ping: when a scheduled ping lands in an already-open window, a
  one-shot launchd job re-opens coverage just after that window ends
  (`CLAUDE_SESSION_PING_BACKUP_BUFFER`, default 120s), re-chaining until a
  fresh window opens; suppressed past `CLAUDE_SESSION_PING_BACKUP_CUTOFF`
  (default 23:02).

### Fixed
- Routine DarkWake read timeouts on `getUpdates` are no longer logged
  (~1–3/hour of noise); genuine failures still log and count toward the
  outage alert.

## [2.1.0] - 2026-07-16

### Changed
- Schedule shifted two minutes later (04:02 / 09:02 / 14:02 / 19:02) so pings
  land clear of the previous window's exact expiry.

## [2.0.1] - 2026-07-16

Sleep resilience.

### Fixed
- Windows are no longer silently missed when the Mac sleeps through a target:
  a run is accepted up to `CLAUDE_SESSION_PING_GRACE_MINUTES` late, with
  state preventing a double-ping.
- The Telegram daemon's launch agent now sets `PATH`, so it can find `claude`
  and report the real window instead of the schedule estimate.
- Long-poll read timeouts (one per DarkWake) no longer trigger the "polling
  has failed" alert; genuine errors still do.

## [2.0.0] - 2026-07-16

Telegram notifier + Q&A bot, plus real usage-window reporting, built on top
of the v1.0.0 keepalive core.

### Added
- Telegram notifications on every keepalive outcome, distinguishing "opened a
  new window" from "landed in an already-open window", with the true window
  start/end parsed from `claude -p "/usage"` (free: no quota, opens no
  window), a weekly-limit warning at ≥ 80%, and a usage link.
- Telegram Q&A daemon (`scripts/telegram_qa_daemon.py`): answers usage and
  schedule questions locally from live usage/state/schedule, with an OpenAI
  fallback (live usage included as context) for anything else. Installed as
  its own launchd job by `install.sh` when `TELEGRAM_BOT_TOKEN` +
  `TELEGRAM_CHAT_ID` are set.
- `scripts/usage_lib.py` (pure parser) + `scripts/claude_usage.py` (IO
  wrapper) with unit tests; `scripts/mock_session_ping.sh` for deterministic
  mock runs; `CLAUDE_SESSION_PING_MAX_RETRIES` / `_RETRY_DELAY` overrides;
  `.env.example`.

### Fixed
- Launch agent sets `PATH` and `USER`/`LOGNAME` explicitly — launchd's
  minimal environment made every scheduled ping fail silently ("claude not
  found", then "Not logged in").
- Config, state, and logs all default to project-local paths (`.env`,
  `.claude-session-ping/`, `logs/`) instead of the home directory.
- Q&A robustness: intent matching no longer false-positives on substrings
  ("weekend" ≠ window end); OpenAI errors can't crash the poll loop; the
  Responses API parse tolerates leading reasoning items; `parse_env_text`
  handles `export` lines and inline comments.
- Notifications no longer render a link-preview card; `install.sh` requires
  both `TELEGRAM_CHAT_ID` and the token before installing the daemon.

## [1.0.0] - 2026-07-13

Initial release: a `launchd`-based keepalive ping, no LLM required to decide
*when* to fire.

### Added
- `launchd/com.claude-session-ping.plist` template firing daily at 04:00,
  09:00, 14:00, and 19:00.
- `scripts/claude_session_ping.sh`: schedule check, keepalive ping, up to 4
  retries on a usage-limit/blocked response.
- `install.sh`, MIT license, initial README.

[Unreleased]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.2.2...HEAD
[2.2.2]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.2.1...v2.2.2
[2.2.1]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.2.0...v2.2.1
[2.2.0]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.0.1...v2.1.0
[2.0.1]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/mattsebastianh/claude-session-ping/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/mattsebastianh/claude-session-ping/releases/tag/v1.0.0
