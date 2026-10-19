<div align="center">

# tcli · Telegram in your terminal

**Read chats, send messages, and follow conversations with your personal Telegram account.**

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Telethon](https://img.shields.io/badge/Telegram-Telethon-26A5E4?logo=telegram&logoColor=white)
![CLI](https://img.shields.io/badge/Interface-CLI-222222)
[![GitHub stars](https://img.shields.io/github/stars/BYxarek/telegram-cli?style=flat)](https://github.com/BYxarek/telegram-cli/stargazers)

**English** · [Русский](https://github.com/BYxarek/telegram-cli/blob/main/README.ru.md)

[Features](#features) · [Installation](#installation) · [Quick start](#quick-start) · [Commands](#commands) · [AI agents](#ai-agents)

</div>

[Changelog](CHANGELOG.md)

This project was created specifically to help AI agents manage Telegram chats more effectively: read conversations, send messages and files, and work with attachments through a CLI.

```console
tcli                          # Show help and setup steps
tcli chats                    # Find a chat
tcli read "Work chat"         # Read recent messages
tcli send me "Remember the meeting"
tcli watch "Work chat"        # Follow new messages
```

`tcli` is a local Python CLI built with [Telethon](https://docs.telethon.dev/).
It uses the Telegram API and acts as your personal account. You need your own
`API_ID` and `API_HASH`; no bot token is used.

## Features

- **Chat list:** IDs, names, and unread counts.
- **Message history:** recent messages, oldest first, with timestamps and sender IDs.
- **Attachments:** upload photos, videos, audio, and documents; inspect metadata and download by message ID.
- **Text messages:** send to Saved Messages, usernames, or chats where you can post.
- **Live updates:** follow new messages in one chat until `Ctrl+C`.
- **Local configuration:** enter API credentials once using `tcli config`.
- **Saved login:** reuse your local session on later runs.
- **Built-in help:** `tcli`, `tcli help`, and `tcli help <command>` work without an account.
- **AI agent skills:** CLI usage instructions for Codex and Claude Code.

Reading history does not mark messages as read. Outgoing text is literal by default;
use `--format markdown` or `--format html` for formatting. Terminal control characters
are removed from displayed messages.

### Current scope

One local account session, text and attachments, human-readable output, and optional JSON.
Message deletion and a full-screen interface are not available.

## Extended history and automation

```console
tcli capabilities
tcli chats --search "work" --limit 10 --json
tcli info "Chat" --json
tcli read "Chat" --since 2026-10-01 --until 2026-10-08 --json
tcli read "Chat" --after 123 --before 999 --from-user @username --json
tcli read https://t.me/example/123 --json
tcli pinned "Chat" --limit 20 --json
tcli topics "Forum group" --limit 50 --json
tcli read "Forum group" --topic 123 --json
tcli thread "Chat" 123 --depth 10 --json
tcli export "Chat" --since 2026-10-01 --until 2026-10-08 --output history.md --json
tcli read "Chat" --after 123 --limit 1000 --jsonl
tcli watch "Chat" --edits --json
tcli send me "Quiet reminder" --silent
tcli send me "Reminder" --schedule 2026-10-20T10:00:00+03:00 --json
tcli scheduled me --json
tcli scheduled me --cancel 123 --json
tcli download "Chat" --batch --filename "*.zip" --limit 5 --max-bytes 104857600 --output-dir downloads --json
tcli download "Chat" 123 --output archive.zip --resume --json
```

All IDs, recipients and paths above are examples. Sends, scheduling, cancellation, exports and
downloads require an explicit user request; development and tests do not authorize live operations.

- `--since` is inclusive and `--until` exclusive. Dates mean midnight UTC; timestamps require
  a timezone offset or `Z`. `--after` and `--before` exclude their message IDs. Combine these with
  text/media/filename filters. `--from-user` accepts `me`, an `@username`, or a known numeric user ID.
  `--id`/`--around` exclude all these filters.
- `chats --search` matches title substrings without case sensitivity; `--limit` counts matches.
  It does not change exact-title chat selection or choose between duplicate titles.
- `info` returns ID, name, type, username, description, forum status and current-account permissions.
  User-chat permissions are null. Group/channel permissions summarize known rights, not a guarantee
  that a particular message will be accepted by Telegram.
- `read` accepts public `https://t.me/username/ID` and private `https://t.me/c/channel/ID` links
  instead of CHAT, without `--id`, `--around`, `--before` or other history filters. Private links
  do not grant access; the account must already have access. Invite, topic and comment links are unsupported.
- Message JSON adds `sender_name` (nullable), from cached entities without per-message requests.
  Human output includes the name alongside the numeric sender ID; outgoing messages still show `you`.
- `pinned` returns up to `--limit` pinned messages, oldest first within the selected page.
  `topics` lists forum topic IDs, titles, dates, top-message IDs, closed/pinned status and unread counts.
  To continue a topics page, pass the final row's `id`, `top_message`, and `offset_date` as
  `--before-topic`, `--offset-id`, and `--offset-date` together. Telegram controls topic ordering; offset_date is null if its source message is unavailable.
  `read --topic ID` reads replies in that topic, excluding its starter. General (topic 1) is selected
  locally from forum history, including ordinary replies and excluding other topic starters. Telegram ignores search filters
  on reply requests, so `--topic` rejects `--search`, `--media` and `--from-user`; filename/date/ID filters work.
- `thread` follows parents in the same chat, returning root-to-selected order. `--depth` bounds
  the total number of messages. Missing parents, cross-chat replies, cycles and depth limits produce
  a `thread_incomplete` notice on stderr; they never silently imply a complete conversation.
- `export` writes selected history oldest first to a new UTF-8 Markdown file, with timestamps,
  authors, quoted message bodies, attachment metadata and message links where available. The parent
  directory must exist; existing files are never replaced. Attachments themselves are not exported.
  It supports the history filters and `--limit` (default 1000). Output contains private conversation data.
- `read --jsonl` streams one message per line, oldest first, without buffering the whole history.
  Unlike ordinary `read`, `--limit` selects the oldest matches after the given bounds, not the latest.
  Use the last emitted ID with `--after` for the next page. It excludes `--json`, `--id`, and `--around`.
  Without `--after`, scanning starts at the beginning of history; date-only lower bounds can require
  scanning older pages. Check the exit status: interrupted streams contain only partial results.
  Errors and filename scan notices go to stderr as JSON; stdout contains only message rows.
- Filename searches report `scan_limit_reached` on stderr when the bound is reached before enough
  matches are found. An empty result does not prove the file is absent from the rest of the chat.
- `watch --edits` combines Telegram edit updates with polling the latest 100 messages, so edits
  from another CLI process sharing the session are detected too. With `--json`, every row adds
  `event="new"|"edit"`; ordinary watch JSONL remains unchanged. Edits include message ID and full current
  text, with duplicate updates suppressed. Intermediate edits between polls may be collapsed; older
  edits require Telegram updates. There is no edit backfill across restarts or disconnected periods.
- `send --silent` requests no notification sound. `--schedule` requires a future timestamp with
  time and timezone; delivery is scheduled by Telegram, so the CLI need not remain running.
  Both options work for text, individual files and albums. Telegram enforces scheduling limits.
  A scheduling result confirms scheduling, not delivery. Inspect `scheduled CHAT` before retrying an
  uncertain operation. Scheduled IDs differ from delivered message IDs.
- `scheduled CHAT` lists scheduled messages in delivery-time order. `--cancel ID [ID ...]` verifies
  that every requested ID is scheduled in that chat before requesting cancellation. It never deletes
  delivered history. Telegram may deliver a message between listing and cancellation.
- `download --batch` omits the positional message ID and requires an existing `--output-dir` and
  `--max-bytes`. It supports history filters, filename scans and `--limit` (attachment count, default 20).
  Selection is newest first. All selected sizes and destination collisions are checked before downloads;
  exceeding the byte budget fails without downloading. Unknown sizes are rejected. Filenames use
  message IDs and safe extensions, as with albums. Completed downloads survive a later failure.
- `download --resume` works for single, album and batch downloads. It retains `.part` and `.part.json`
  files after failure/cancellation, checks media identity, size and the partial file's SHA-256 before
  appending, and publishes only a complete file without overwriting. Repeat a single-file command to continue.
  For a partially completed album/batch, resume the failed ID with --output pointing to its ID-based
  path, or narrow batch bounds to unfinished files; completed destinations still cause collision errors.
  A `.part.lock` prevents concurrent resume operations; after a hard process crash, stop the old process
  before removing its stale lock. Do not change partial files or manifests. Publication requires a
  filesystem supporting hard links. Ordinary downloads still remove failed partial files.
- `capabilities` outputs command/parameter metadata and JSON output descriptions without credentials,
  file writes or network access. It describes parser options; the usage constraints above still apply.
- SQLite locked/busy errors explain how to stop conflicting CLI processes and locate the session with
  `doctor`; never delete the session or SQLite sidecars to resolve a lock.

## Installation

Requires **Python 3.10+**, pip, and access to Telegram.

### Published package and updates

Install the isolated CLI and its user skills:

```console
pipx install telegram-cli-local
tcli skills install
tcli --version
```

Update the CLI, then refresh the bundled skills:

```console
pipx upgrade telegram-cli-local
tcli skills install
```

For an existing Python virtual environment, use `python -m pip install --upgrade telegram-cli-local` instead.
Install pipx using its [official instructions](https://pipx.pypa.io/stable/installation/).
GitHub source installations are also supported:

Clone the repository, or download and extract **Code → Download ZIP** on GitHub:

```console
git clone https://github.com/BYxarek/telegram-cli.git
cd telegram-cli
```

### Windows · PowerShell

Install for your user:

```powershell
py -m pip install --user -e .
tcli
```

If pip is missing, first run `py -m ensurepip --upgrade --user`.

<details>
<summary>PowerShell cannot find tcli</summary>

Add the user Python scripts directory to `PATH`:

```powershell
$tcliScriptsDir = (py -c "import sysconfig; print(sysconfig.get_path('scripts', scheme='nt_user'))").Trim()
$tcliUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (($tcliUserPath -split ';') -notcontains $tcliScriptsDir) {
    $tcliUpdatedPath = (($tcliUserPath + ';' + $tcliScriptsDir).Trim(';'))
    [Environment]::SetEnvironmentVariable('Path', $tcliUpdatedPath, 'User')
}
$env:Path = $tcliScriptsDir + ';' + $env:Path
tcli
```

New terminals receive the saved `PATH`. Restart an existing terminal or its parent application if needed.

</details>

The installed command works from any directory. Editable installation (`-e`) uses this source folder;
if you move it, reinstall from its new location.

### Linux / macOS · bash or zsh

Use a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
source .venv/bin/activate
tcli
```

After activation, `tcli` works from any directory in that terminal.
In a new terminal, activate the environment again using its full path,
or run `/path/to/telegram-cli/.venv/bin/tcli` directly.
Some Linux distributions require installing the `venv` component separately through the OS package manager.

## Quick start

### 1. Get your API credentials

Sign in at [my.telegram.org](https://my.telegram.org), open **API development tools**, and create an application.
Follow the [official Telegram guide](https://core.telegram.org/api/obtaining_api_id) to obtain `api_id` and `api_hash`.

### 2. Configure and sign in

```console
tcli config
tcli login
```

`config` asks for your API credentials and saves them locally. API hash input is hidden.
It does not connect to Telegram. Run it again to replace credentials; invalid input leaves existing settings intact.

`login` asks for your international phone number, Telegram login code, and 2FA password if enabled.
Code and password input is hidden. The session is saved for later commands.

### 3. Read and send

```console
tcli chats
tcli read "Chat name" --limit 10
tcli send me "Hello from tcli!"
```

`me` means your own **Saved Messages**. Messages are sent from your personal account.

## Help

```console
tcli                   # General help, setup steps, and examples
tcli help              # Same general help
tcli help send         # How to send a message
tcli read --help       # Read options and examples
```

Help does not load configuration, create a session, or connect to Telegram.
In an interactive terminal, `tcli`, `tcli help`, `tcli --help`, and `tcli -h` also check PyPI for
a newer stable release. The check uses a one-second network timeout and caches results (including
failed checks) for 24 hours in the data directory's `cache/update-check.json`.
Available updates are reported on stderr with upgrade instructions; no update is installed automatically.
Network/cache errors are silent. Piped output, command-specific help, and `--version` skip the check.
Set `TCLI_NO_UPDATE_CHECK=1` to disable it entirely.
Running `tcli` without a command succeeds instead of showing a missing-command error.
Unknown commands and invalid arguments still return an error.

## Commands

### Attachments

```console
tcli send me --attach photo.jpg --caption "Photo"
tcli send me --attach video.mp4
tcli send me --attach music.mp3
tcli send me --attach archive.zip --as-file
tcli send me --attach photo.jpg --caption "<b>Photo</b>" --format html --reply-to 123
tcli read me --limit 10
tcli download me 123 --output photo.jpg
```

`--attach` uploads one or more existing local files in order, including binary documents such as `.exe`, `.apk`, and `.zip`. Telethon selects the media representation;
`--as-file` forces a document. Telegram enforces supported formats and file size limits.
`--caption` and `--as-file` require `--attach`. `--file` still sends UTF-8 file contents as text.
`read` and `watch` show attachment type, filename (when available), MIME type, size, and caption.
Their JSON includes `media`: null or an object with `type`, `name`, `mime_type`, `size` (bytes),
`ext`, `width`, `height`, and `duration` (seconds); unavailable fields are null.
Other media, such as link previews, can have `has_media=true` with `media=null`.

Use a real message ID from the selected chat with `download`. `--output` is an explicit file path;
its parent must exist. Existing files are never overwritten, remote filenames never determine
local paths, and failed or cancelled downloads remove the partial file.
Photos download as stored by Telegram. Open the downloaded file in a local application to view it;
the CLI displays metadata. Deletion and inline media playback are not supported.


| Command | Purpose |
| --- | --- |
| `tcli config` | Save or replace API credentials |
| `tcli login` | Sign in to your personal account |
| `tcli chats --limit 100` | List up to 100 chats; default: 50 |
| `tcli read "Chat" --limit 20` | Read recent messages; default: 20 |
| `tcli send "Chat" "Text"` | Send one plain-text message |
| `tcli forward "Source" 123 "Destination"` | Forward a message or attachment without local transfer |
| `tcli edit "Chat" 123 "New text"` | Edit an outgoing message or attachment caption |
| `tcli download "Chat" 123 --output file.zip` | Download one attachment without overwriting |
| `tcli watch "Chat"` | Follow new messages until `Ctrl+C` |
| `tcli logout` | Revoke this CLI session, keeping API credentials |
| `tcli help [COMMAND]` | Show general or command-specific help |
| `tcli doctor [--connect]` | Check local configuration/session; optionally test connectivity and authorization |
| `tcli skills install [--agent auto\|all\|codex\|claude]` | Install/update bundled user skills without Telegram access |
| `tcli --version` | Show the installed CLI version |

### JSON, search, pagination, and replies

```console
tcli chats --json
tcli read "Chat" --json --limit 20
tcli watch "Chat" --json
tcli read "Chat" --search "meeting" --before 12345 --limit 20
tcli send "Chat" "Reply text" --reply-to 12345
tcli send me --file message.txt
```

`chats --json` and `read --json` return JSON arrays, including `[]` for empty results.
Chat objects contain `id`, `name`, and `unread_count`. Message objects contain `id`,
`chat_id`, `date` (ISO 8601 with offset), `sender_id` (nullable), `out`, `text`, `has_media`, `media`, `reply_to_id`, and `grouped_id`.
JSON preserves original text, including Unicode and control characters as escaped sequences;
human-readable output removes terminal controls. Media-only messages have empty JSON text.
`watch --json` emits one message object per line (JSONL), flushing immediately.
Watch status and errors go to stderr, keeping stdout machine-readable.

`--search` uses Telegram's text search within the selected chat. Results remain oldest first
within each returned page. `--before ID` excludes that ID and returns older messages;
use the smallest returned message ID for the next page. It can be combined with search.
`--before` and `--reply-to` require positive message IDs from the same chat.

`send` requires exactly one source: positional text, `--stdin`, `--file PATH`, or `--attach PATH`.
Files and stdin use UTF-8 (an optional BOM is accepted). Stdin is read until EOF;
empty or whitespace-only input is rejected before connecting. Line breaks and surrounding
whitespace are kept in plain mode. `--file` sends the file's text, not an attachment.
All text sources support `--reply-to` and `--format`.

### Message context, media filters, and editing

```console
tcli read "Chat" --id 123 --json
tcli read "Chat" --media document --limit 20 --json
tcli read "Chat" --media photo --search "holiday" --before 123 --json
tcli edit "Chat" 123 "Corrected text" --json
tcli edit "Chat" 123 --file caption.html --format html
```

`read --id ID` returns that message (a one-element array with `--json`) or a
`message_not_found` error. It cannot be combined with `--around`, `--before`, or search/media/filename filters;
`--limit` applies only to history. Message output includes `reply_to_id` and `grouped_id`
(null when absent). Use `read --id` with `reply_to_id` to retrieve the original message.
Human-readable output also shows reply and album IDs.

`--media` uses Telegram's filters: `photo`, `video`, `document`, `audio` (music),
`voice`, `video_note`, or `gif`. It combines with search, pagination, and limits.
Search follows Telegram's text-search rules; it is not a local filename or extension search.

`edit CHAT ID` changes the text or attachment caption of an outgoing message.
Use exactly one text source: positional text, `--stdin`, or `--file PATH`, with optional
`--format plain|markdown|html`. Empty text is rejected. Attachments themselves are not replaced.
Telegram enforces edit permissions and time limits; an unchanged message may return an RPC error.

### Multiple files and albums

```console
tcli send me --attach app.apk archive.zip --caption "App" --caption "Archive" --json
tcli send me --attach first.jpg second.jpg --album --caption "First" --caption "Second" --json
tcli download me 123 --album --output-dir downloads --json
```

Without `--album`, files are sent individually in the supplied order. Repeat `--caption`
for each file in order; omitted captions are empty (use `--caption ""` to skip a caption).
All files are checked before connecting. `--reply-to`, `--format`, and `--as-file` apply to all.
`--album` sends 2-10 attachments as one group through Telethon. Use compatible Telegram album
media; Telegram may reject incompatible combinations. A single caption belongs to the first item.

Download an album using any of its message IDs. `--album` requires `--output-dir`, which must
already exist. Files are saved oldest first as `<message_id><extension>` (for example `123.jpg`),
using `.bin` for unknown or unsafe extensions. Remote names cannot escape the directory.
All destinations are checked for collisions before downloading; existing files are never overwritten.
If a later transfer fails, completed files remain and the current partial file is removed.
JSON results keep each attachment's caption in `text` and its album ID in `grouped_id`.
Deleted or inaccessible album items cannot be downloaded.

### JSON results and errors

`send --json` outputs `{"ok": true, "messages": [...]}` using the same message objects as `read`.
`edit --json` outputs `{"ok": true, "message": {...}}`.
`download --json` outputs `{"ok": true, "files": [...]}`; each file has `message_id`, `chat_id`,
`path` (absolute), `text` (caption), and `grouped_id` (nullable).
Existing `chats`/`read` arrays and `watch` JSONL remain unchanged apart from added message fields.

Commands with `--json` emit handled errors as one JSON object on stderr, including argument errors:

```json
{"ok": false, "error": {"code": "flood_wait", "message": "Telegram rate limit: wait 17 seconds before trying again.", "retry_after": 17}}
```

`retry_after` is seconds for FloodWait and null otherwise. Stable codes include
`usage_error`, `invalid_input`, `configuration_error`, `unauthorized`, `chat_not_found`,
`ambiguous_chat`, `message_not_found`, `not_outgoing`, `edit_failed`, `not_an_album`,
`no_attachment`, `file_exists`, `flood_wait`, `rpc_error`, `network_error`, `io_error`,
`database_error`, and `cancelled`. RPC errors also include `rpc_type` and `rpc_code`.
Exit codes remain 1 for handled failures, 2 for usage errors, and 130 for interruption.

After a partial multi-file operation, the error object also includes `messages` or `files`
with confirmed completed results. With progress enabled, stderr may contain progress events before the error.
The CLI does not retry operations automatically; after an uncertain
send/edit network failure, inspect history before retrying. Watch status still goes to stderr as text.

### Filename search, conversation context, forwarding, and progress

```console
tcli read "Chat" --filename "*.apk" --scan-limit 1000 --limit 5 --json
tcli read "Chat" --filename "backup-*.zip" --before 12345 --json
tcli read "Chat" --around 123 --context 5 --json
tcli forward "Source chat" 123 "Destination chat" --json
tcli send me --attach archive.zip --progress --json
tcli download me 123 --output archive.zip --progress --json
```

`--filename` matches original attachment names locally using case-insensitive glob patterns
(`*`, `?`, `[abc]`). Messages without a filename do not match. `--scan-limit` bounds the newest
messages inspected (default: 1000, after any Telegram search/media filters); `--limit` bounds
matching results. Results remain oldest first. Increase the scan limit or use `--before` for older
history; no matches within the scan does not prove that the entire chat has no matching file.
`--scan-limit` requires `--filename`, which also combines with `--search`, `--media`, and `--before`.

`--around ID` returns the selected message and up to `--context N` messages on each side
(default: 5). It uses chat history order, not consecutive IDs. At history boundaries fewer messages
may be available. Missing target messages produce `message_not_found`. `--around` excludes
`--id`, `--before`, and search/media/filename filters. `--context` requires `--around`;
`--limit` applies only to history and filename search.

`forward FROM_CHAT ID TO_CHAT` forwards one message, including its attachment and caption,
through Telegram without downloading or uploading files. Both selectors use the normal chat rules.
The result has the same JSON shape as `send`. Forward attribution is retained; Telegram may reject
protected content or destinations where you cannot post. Forward only on explicit user instruction;
a development/test request does not authorize a live forward. Inspect history before retrying an
uncertain network failure. For negative IDs, place options before `--`, then all positional arguments.

Upload/download progress is enabled automatically when stderr is a terminal. `--progress` enables
it when redirected; `--no-progress` disables it. These flags apply to attachment sends and downloads.
Progress includes transferred/total bytes, percentage, and average bytes per second; album upload
progress is weighted by local file sizes. Updates are throttled to twice per second, plus completion.
With `--json`, stderr receives JSONL events with `event="transfer_progress"`, `operation`, `file`,
`transferred_bytes`, `total_bytes`, `percent` (null for unknown/zero size), and `bytes_per_second`.
Errors are still JSON objects on stderr; distinguish them from progress by `ok=false`.
Stdout contains only the final result. Progress measures transfer work, not confirmed delivery;
cached uploads may produce no progress events.

### Text formatting

```console
tcli send me '**Bold** __italic__ ~~strike~~ `code` [link](https://example.com)' --format markdown
tcli send me '<b>Bold</b> <i>italic</i> <u>underline</u> <s>strike</s>' --format html
tcli send me '<blockquote>Quote</blockquote>' --format html
tcli send me --file message.html --format html --reply-to 12345
```

`--format plain` (the default) sends markup literally. Markdown is Telethon's dialect,
not Telegram Bot API MarkdownV2: `**bold**`, `__italic__`, `~~strike~~`, backticks for code,
triple backticks for code blocks, and `[label](URL)`. Use HTML for underline and nested
formatting. Supported HTML includes `<b>`, `<strong>`, `<i>`, `<em>`, `<u>`, `<s>`, `<del>`,
`<code>`, `<pre>`, `<a href="URL">`, and `<blockquote>` (also `expandable`).
`<pre><code class="language-python">...</code></pre>` specifies a code-block language.
HTML escapes use `&lt;`, `&gt;`, and `&amp;`. Use actual line breaks, not `<br>`.
The installed Telethon parser does not support spoiler markup; unknown HTML tags are ignored.
Telegram may trim surrounding whitespace when parsing formatted text.

PowerShell pipeline example (ensure the shell sends UTF-8):

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Get-Content -Raw -Encoding utf8 message.txt | tcli send me --stdin
```

`doctor` prints configuration/session paths, validates the effective API credentials without
showing their values, and checks session-file presence without connecting or creating files.
It exits 1 on invalid configuration or a missing session. File presence does not prove authorization.
`doctor --connect` requires an existing session and additionally connects to Telegram and checks
authorization without prompting for login or reading chats. It may update the existing Telethon session.

### Choosing a chat

Use `me`, `@username`, an exact chat name, or an ID from `tcli chats`.
Chat names are case-sensitive. If names are duplicated, use the ID.
Partial name matching and `t.me` links are not supported.

Put `--` before a negative ID, after all options:

```console
tcli read --limit 10 -- -1001234567890
tcli send -- -1001234567890 "Message to a group"
tcli watch -- -1001234567890
```

The ID above is an example; use an ID from your chat list. Limits must be positive integers.
`watch` records the latest message ID at startup and checks for newer messages every two seconds.
It includes outgoing messages from another CLI process, including Saved Messages, and outputs
each fetched batch oldest first without a fixed batch limit. Already existing messages are not printed.
Messages deleted before the next check cannot be displayed. Open a second terminal to send while watching.
Session changes are committed between checks so the watcher does not hold a SQLite write lock while idle.
Timestamps use your computer's local time zone.

## Configuration and session

| Platform | Data directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\telegram-cli\` |
| Linux / macOS, when `LOCALAPPDATA` is absent | `~/.local/share/telegram-cli/` |

`config.json` stores API credentials; `account.session` stores the Telethon session.
Both are kept outside the source directory and are independent of your working directory.

For compatibility, `TG_API_ID` and `TG_API_HASH` environment variables override the file.
If either variable is present, both are required; values from different sources are never mixed.
`.env` files are not loaded automatically.

**The session file grants access to your account.** Do not publish it, your API hash, login codes, or password.
Configuration is stored unencrypted. New configuration files use permissions `0600` on POSIX;
Windows inherits access permissions from the profile directory.
If a session leaks, revoke it in Telegram: **Settings → Devices**.
Outgoing message text may be saved in your shell history.

## AI agents

| Agent | Skill | Invocation |
| --- | --- | --- |
| Codex | [.agents/skills/telegram-cli/SKILL.md](https://github.com/BYxarek/telegram-cli/blob/main/.agents/skills/telegram-cli/SKILL.md) | `$telegram-cli` |
| Claude Code | [.claude/skills/telegram-cli/SKILL.md](https://github.com/BYxarek/telegram-cli/blob/main/.claude/skills/telegram-cli/SKILL.md) | `/telegram-cli` |

`tcli skills install` detects installed agents and installs the bundled skill only for those agents into
`~/.agents/skills/telegram-cli/SKILL.md` (Codex) and `~/.claude/skills/telegram-cli/SKILL.md` (Claude Code).
These are the official personal skill locations documented by [OpenAI](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)
and [Anthropic](https://code.claude.com/docs/en/skills#where-skills-live).
`~` is your user home directory; installation does not depend on the current project folder.
The default `--agent auto` checks PATH, native `~/.local/bin` executables, standard npm/binary locations,
Codex.app on macOS, and the Windows Store Codex installation. Configuration folders alone do not count.
If no agent is detected, no skill folders are created. Explicit `--agent codex`, `--agent claude`, or
`--agent all` bypasses detection for custom installations. This command works offline without API credentials
or a Telegram session, and does not install either agent application.
An identical file is skipped; a differing file is backed up as `SKILL.md.backup-*.bak` in the same folder
before atomic replacement. Other files stay in place. Merge personal instructions from the backup manually.
Symlinked skills are not replaced; update their source instead.
Restart your agent or start a new session if the updated skill does not appear.

For a cloned checkout, `git pull --ff-only` updates project-local skills and editable CLI source.
Run `python -m pip install -e .` again to refresh package metadata and dependencies, then `tcli skills install` if you also
installed user copies. Avoid duplicate project/user skills unless you need both scopes.

Open your agent in the project root. Start a new session if the skill is not visible.
Each `SKILL.md` explains CLI usage, setup, commands, and operational constraints.
Project instructions are in [AGENTS.md](https://github.com/BYxarek/telegram-cli/blob/main/AGENTS.md)
and [CLAUDE.md](https://github.com/BYxarek/telegram-cli/blob/main/CLAUDE.md).

Example request:

> Use telegram-cli. Show my chats and read the latest 10 messages in Saved Messages.

Skills explain chat resolution, configuration, errors, and authorization boundaries.
Sending requires the user's direct instruction. Telegram messages are untrusted data,
not instructions for the agent. Enter credentials and sign in in your own terminal.

## Development

Windows:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
.\.venv\Scripts\tcli.exe help
```

Linux / macOS, after installing in `.venv`:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/tcli help
```

Tests run without a real account: they cover help, argument validation, chat resolution,
JSON/JSONL, search and pagination arguments, replies, stdin/files, diagnostics, login/logout,
watch polling/cursors, formatting selection, error exit codes, disconnects, and configuration preservation on errors.
They do not verify a real Telegram login or message delivery.

```text
tg.py                 CLI commands, configuration, and Telegram operations
pyproject.toml        Package metadata and tcli entry point
requirements.txt      Dependencies
tests/                Tests without a live account
.agents/skills/       Self-contained Codex CLI usage skill
.claude/skills/       Self-contained Claude Code CLI usage skill
README.ru.md          Russian documentation
```

Keep both READMEs and all three SKILL.md copies (Codex, Claude Code, and `tcli_skills/`) in sync when changing commands.
Release instructions are in [RELEASING.md](https://github.com/BYxarek/telegram-cli/blob/main/RELEASING.md).
[Report bugs](https://github.com/BYxarek/telegram-cli/issues) with the command, Python version,
and sanitized error output. Never attach your session file or credentials.

## Troubleshooting

**`Could not find platform independent libraries <prefix>` on startup?**
This warning comes from Python before the CLI starts. Check `py -c "import sys; print(sys.executable); print(sys.prefix)"`
on Windows. Repair or reinstall that Python installation so its standard library and extension modules
are present alongside the interpreter; avoid moving `python.exe` on its own.
Then reinstall the CLI using that interpreter. If a virtual environment points to a removed installation,
recreate it and reinstall the project. Do not hide stderr: it also contains real CLI errors.

**Chat not found?** Use an exact name, `@username`, or an ID from `tcli chats`.
Increase `--limit` if your chat is not in the first page.

**Telegram asks you to wait?** Wait for the reported FloodWait period.
After a connection failure, check recent history before retrying a send to avoid duplicates.

**Can I use a bot?** This CLI supports personal accounts only.

**Can I still run the Python file?** Yes: `python tg.py ...` from the project root
with dependencies installed uses the same commands.

## License

Copyright 2026 BYxarek. Licensed under [Apache-2.0](https://github.com/BYxarek/telegram-cli/blob/main/LICENSE).
