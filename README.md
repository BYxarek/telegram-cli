<div align="center">

# tcli · Telegram in your terminal

**Read chats, send messages, and follow conversations with your personal Telegram account.**

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Telethon](https://img.shields.io/badge/Telegram-Telethon-26A5E4?logo=telegram&logoColor=white)
![CLI](https://img.shields.io/badge/Interface-CLI-222222)
[![GitHub stars](https://img.shields.io/github/stars/BYxarek/telegram-cli?style=flat)](https://github.com/BYxarek/telegram-cli/stargazers)

**English** · [Русский](README.ru.md)

[Features](#features) · [Installation](#installation) · [Quick start](#quick-start) · [Commands](#commands) · [AI agents](#ai-agents)

</div>

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
- **Text messages:** send to Saved Messages, usernames, or chats where you can post.
- **Live updates:** follow new messages in one chat until `Ctrl+C`.
- **Local configuration:** enter API credentials once using `tcli config`.
- **Saved login:** reuse your local session on later runs.
- **Built-in help:** `tcli`, `tcli help`, and `tcli help <command>` work without an account.
- **AI agent skills:** project instructions for Codex and Claude Code.

Reading history does not mark messages as read. Outgoing text is sent literally,
without Markdown parsing. Terminal control characters are removed from displayed messages.

### Current scope

One local account session, text messages, and human-readable output.
Media is shown as a placeholder; uploading and downloading attachments are not implemented.
Message editing, deletion, replies to specific messages, a full-screen interface,
and JSON output are not available.

## Installation

Requires **Python 3.10+**, pip, and access to Telegram.
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
Running `tcli` without a command succeeds instead of showing a missing-command error.
Unknown commands and invalid arguments still return an error.

## Commands

| Command | Purpose |
| --- | --- |
| `tcli config` | Save or replace API credentials |
| `tcli login` | Sign in to your personal account |
| `tcli chats --limit 100` | List up to 100 chats; default: 50 |
| `tcli read "Chat" --limit 20` | Read recent messages; default: 20 |
| `tcli send "Chat" "Text"` | Send one plain-text message |
| `tcli watch "Chat"` | Follow new messages until `Ctrl+C` |
| `tcli logout` | Revoke this CLI session, keeping API credentials |
| `tcli help [COMMAND]` | Show general or command-specific help |

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
`watch` displays only new messages. Open a second terminal to send while watching.
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
| Codex | [.agents/skills/telegram-cli/SKILL.md](.agents/skills/telegram-cli/SKILL.md) | `$telegram-cli` |
| Claude Code | [.claude/skills/telegram-cli/SKILL.md](.claude/skills/telegram-cli/SKILL.md) | `/telegram-cli` |

Open your agent in the project root. Start a new session if the skill is not visible.
See [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md), and the [agent guide](docs/agent-guide.md).

Example request:

> Use telegram-cli. Explain the commands and run tests without signing in to Telegram.

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
message handling with a fake client, and configuration preservation on errors.
They do not verify a real Telegram login or message delivery.

```text
tg.py                 CLI commands, configuration, and Telegram operations
pyproject.toml        Package metadata and tcli entry point
requirements.txt      Dependencies
tests/                Tests without a live account
docs/agent-guide.md   Agent reference
.agents/skills/       Codex skill
.claude/skills/       Claude Code skill
README.ru.md          Russian documentation
```

Keep both READMEs and the agent guide in sync when changing commands.
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
