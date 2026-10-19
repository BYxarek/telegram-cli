# Changelog

All notable project changes are recorded here. New changes go under `Unreleased`;
move them into a versioned section when publishing a release.

## Unreleased

No unreleased changes.

## 0.3.0 - 2026-10-19

### Changed

- Reorganize all three repository Telegram CLI skills around task selection, current commands,
  filtering/pagination, scheduling, resumable transfers, JSON events and authorization boundaries.
  Keep repository maintenance separate from installing user skills.

### Fixed

- Detect edits from another CLI process sharing the account session by polling the latest 100
  messages alongside Telegram edit updates. Suppress duplicate edit events and recover new-message
  bursts larger than the polling window.
- Stop dialog iteration as soon as a unique numeric chat ID matches, avoiding unnecessary API pages.

### Added

- History date, sender and ID bounds; chat-title substring search; public/private message-link reads.
- Cached sender names, chat details and permissions, pinned messages, forum topic listing and reads,
  bounded parent reply chains, and Markdown exports with available message links.
- Streaming, paginated history JSONL and explicit filename scan-limit notices on stderr.
- Optional live edit events in watch; silent and server-scheduled sending; scheduled-message listing
  and explicit cancellation with scheduled-ID validation.
- Bounded batch attachment downloads with byte-budget preflight and verified resumable transfers
  using media identity, partial SHA-256 checks, exclusive locks and non-overwriting publication.
- Offline machine-readable capabilities, actionable SQLite lock diagnostics, synchronized usage
  documentation, CI checks for skill identity and fenced CLI examples, and offline regression tests.

## 0.2.1 - 2026-10-18

### Changed

- Focus the Telegram CLI skill on usage; remove project architecture and development instructions from all three copies.

## 0.2.0 - 2026-10-18

### Added

- Bounded, case-insensitive filename glob search with `read --filename` and `--scan-limit`.
- Conversation context with `read --around ID --context N`, using history order even when IDs have gaps.
- Native forwarding of messages and attachments with `forward FROM_CHAT ID TO_CHAT`, including JSON results.
- Automatic terminal upload/download progress, explicit `--progress` / `--no-progress`, JSONL progress
  events on stderr, byte counts, percentages, average transfer speed, and size-weighted album progress.
- Offline regression tests for filename search, context, forwarding restrictions, progress throttling,
  album byte accounting, and clean JSON stdout.
- Include this changelog in source distributions and check new command help in clean wheel installations.

- Upload local photos, videos, audio, and arbitrary binary documents (including
  `.exe`, `.apk`, and `.zip`) with `send --attach`; use `--as-file` to force document upload.
- Attachment captions with text formatting and reply targets; repeat `--caption`
  to provide a separate caption for each attachment.
- Send multiple files individually in order, or group 2-10 compatible attachments
  with `send --album`.
- Download individual attachments by message ID with `download --output` and
  download entire albums with `download --album --output-dir`.
- Safe downloads: exclusive file creation, collision checks, ID-based album filenames,
  safe extensions, and removal of the current partial file after failure or cancellation.
- Display attachment type, filename, MIME type, and size in `read` and `watch`;
  expose dimensions, duration, and other metadata in JSON.
- Read a specific message with `read --id`; include reply and album IDs in message output.
- Filter history with `read --media photo|video|document|audio|voice|video_note|gif`,
  combined with Telegram text search and pagination.
- Edit outgoing message text or attachment captions with `edit`, using positional
  text, UTF-8 files, or stdin and optional Markdown/HTML formatting.
- JSON results for sending, editing, and downloading, including captions and album
  IDs in downloaded-file records.
- Structured JSON errors on stderr with stable codes, FloodWait retry delays,
  RPC details, and confirmed completed results after partial multi-file operations.
- Offline tests for binary uploads, metadata, JSON contracts, filters, edits,
  ordered files and albums, validation, safe downloads, cancellation, and partial failures.
- This changelog and a requirement to update it alongside future changes.

### Changed

- Explain in both READMEs that the project was created specifically to help AI agents
  manage Telegram chats effectively.
- Expand English and Russian documentation and synchronize all three bundled/project
  skill copies with the new commands and automation workflows.
- Clarify the existing official user-skill destinations in installer help and documentation:
  `~/.agents/skills/telegram-cli/SKILL.md` for Codex and
  `~/.claude/skills/telegram-cli/SKILL.md` for Claude Code.
  Default installation still targets detected agents and backs up differing skills.

### Compatibility

- `send --file` still sends UTF-8 file contents as text; `--attach` uploads binary files.
- Existing `chats`/`read` JSON arrays and `watch` JSONL retain their shapes, with
  additional message fields. Human-readable output remains the default.
- Message deletion, inline media playback, and automatic retries remain unsupported.
