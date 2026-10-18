# Changelog

All notable project changes are recorded here. New changes go under `Unreleased`;
move them into a versioned section when publishing a release.

## Unreleased

### 2026-10-18

#### Added

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

#### Changed

- Explain in both READMEs that the project was created specifically to help AI agents
  manage Telegram chats effectively.
- Expand English and Russian documentation and synchronize all three bundled/project
  skill copies with the new commands and automation workflows.
- Clarify the existing official user-skill destinations in installer help and documentation:
  `~/.agents/skills/telegram-cli/SKILL.md` for Codex and
  `~/.claude/skills/telegram-cli/SKILL.md` for Claude Code.
  Default installation still targets detected agents and backs up differing skills.

#### Compatibility

- `send --file` still sends UTF-8 file contents as text; `--attach` uploads binary files.
- Existing `chats`/`read` JSON arrays and `watch` JSONL retain their shapes, with
  additional message fields. Human-readable output remains the default.
- Message deletion, inline media playback, and automatic retries remain unsupported.
