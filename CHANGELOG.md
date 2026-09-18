# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [2.5.0] - 2026-09-18

### Fixed
- Only one ping runs at a time. The label guard could not see a regular target
  and a backup job both firing late after one wake, so the two retried the
  same reopening in parallel and spent both retry budgets on one credit limit.
  A second instance now waits up to `CLAUDE_SESSION_PING_LOCK_WAIT` (600s) for
  the first, skips if that run left a verified window open, and otherwise
  pings; it skips outright if the wait expires.
- `/next`, the "next start" lines in `/status`, and the LLM prompt now report
  what launchd will actually fire, read from the installed plists per question,
  instead of the built-in 07:02/12:02/17:02/22:02 list — so a pending backup
  is the answer when one is scheduled ("Next ping is a one-off backup at
  19:12") and edited target times are followed after `./install.sh`.

### Changed
- The "next next" reply reads "The ping after next is at …" rather than "The
  session window after next starts at …", since that ping may be a backup.

## [2.4.1] - 2026-08-25

### Fixed
- Telegram read timeouts no longer count toward the outage alert on Python 3.9,
  the interpreter launchd runs the daemon under: `socket.timeout` only became
  an alias of `TimeoutError` in 3.10, so the existing filter missed the very
  DarkWake timeouts it was written for. The v2.4.0 `ssl.SSLError` fix covered a
  neighbouring case but not this one.
- A ping is no longer reported as unverified just because the window it opened
  hadn't registered yet: the usage lookup now re-asks up to
  `CLAUDE_SESSION_PING_USAGE_RETRY_ATTEMPTS` (3) times,
  `CLAUDE_SESSION_PING_USAGE_RETRY_DELAY` (15s) apart, when the answer is "no
  session". On 2026-08-25 this cost both the 07:02 and 12:02 verifications
  even though each ping had opened a window. Only "no session" is retried —
  timeouts and unparseable replies still fail immediately.

## [2.4.0] - 2026-08-25

### Added
- Telegram slash commands `/status`, `/usage`, `/window`, `/ends`, `/next`,
  registered with Telegram at startup so they appear in the client's command
  menu. Each maps straight to a local intent, bypassing keyword matching.
- A `status` intent answering broad questions ("session info", "overview",
  "status", "summary") locally with a six-line status block, so the most
  common question costs no LLM call and is formatted identically every time.
  Matched last of all intents, so "quota status" still returns just the quota.

### Changed
- Q&A bot's free-form fallback now calls OpenRouter (`openai/gpt-oss-20b` by
  default) instead of OpenAI, for a cheaper per-question cost. Replace
  `OPENAI_API_KEY`/`OPENAI_MODEL` with `OPENROUTER_API_KEY`/`OPENROUTER_MODEL`
  in `.env`.
- Free-form fallback answers are prompted for emoji-labeled, one-fact-per-line
  output rather than a run-on sentence, so an LLM answer looks like the
  locally-generated ones.

### Fixed
- Telegram polling no longer raises a false outage alert for SSL-layer
  read/handshake timeouts (`ssl.SSLError`) — the same DarkWake dead-socket
  case already filtered for plain `TimeoutError`, since `ssl.SSLError` isn't
  a `TimeoutError` subclass and was slipping through the outage filter.

## [2.3.1] - 2026-08-14

### Fixed
- Usage lookups no longer fail when another process writes to `claude`'s
  stdout — the JSON result is now located line by line instead of decoding the
  whole buffer, which an MCP server's stray output was breaking on roughly
  half of all runs.
- A ping whose window couldn't be verified is reported as unverified rather
  than as a confirmed new window: the old message claimed "✅ Claude session
  window opened at HH:MM" even when the ping had been absorbed by a window
  already running.
- A failed usage lookup no longer deletes the pending backup ping, which
  removed the only cover for an absorbed ping exactly when the run had no way
  to know whether one had happened.

### Added
- `usage lookup unavailable` log lines now carry the reason (`bad_json`,
  `timeout`, `exit_1`, `unparsed`, …) — previously every failure mode shared
  one indistinguishable message.

## [2.3.0] - 2026-08-04

### Changed
- Daily ping schedule moved to **07:02 / 12:02 / 17:02 / 22:02** (was
  04:02 / 09:02 / 14:02 / 19:02) — four 5-hour windows now start with the
  waking day.
- Backup-ping cutoff (`CLAUDE_SESSION_PING_BACKUP_CUTOFF`) default 23:02 →
  **01:59**, and the fire window now **wraps past midnight**: 07:02–01:59 is
  allowed, 02:00–07:01 is the overnight gap. 01:59 is the last minute whose
  5-hour frame closes (06:59) before the 07:02 target, so the day's first ping
  opens a real window instead of being absorbed into an overnight one. The
  22:02 window's own reopening (03:04) therefore stays suppressed by design.
- `pmset repeat wake` guidance in the README now targets 07:02:00.

### Fixed
- Backup-vs-target collision checks now also consider the previous calendar
  day's targets — reachable now that a fire time can legitimately land after
  midnight with a large `CLAUDE_SESSION_PING_BACKUP_BUFFER`.

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

[Unreleased]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.5.0...HEAD
[2.5.0]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.4.1...v2.5.0
[2.4.1]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.4.0...v2.4.1
[2.4.0]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.3.1...v2.4.0
[2.3.1]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.3.0...v2.3.1
[2.3.0]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.2.2...v2.3.0
[2.2.2]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.2.1...v2.2.2
[2.2.1]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.2.0...v2.2.1
[2.2.0]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.0.1...v2.1.0
[2.0.1]: https://github.com/mattsebastianh/claude-session-ping/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/mattsebastianh/claude-session-ping/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/mattsebastianh/claude-session-ping/releases/tag/v1.0.0
