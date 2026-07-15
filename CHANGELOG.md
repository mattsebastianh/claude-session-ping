# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

Telegram notifier + Q&A bot, built on top of the v1.0.0 keepalive core.

### Added
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

[Unreleased]: https://github.com/mattsebastianh/claude-session-ping/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/mattsebastianh/claude-session-ping/releases/tag/v1.0.0
