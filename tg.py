"""Telegram chats and messages from your terminal, using your personal account."""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
from contextlib import ExitStack
from fnmatch import fnmatchcase
from getpass import getpass
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
from http.client import HTTPException
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from telethon import TelegramClient, events, utils
from telethon.errors import FloodWaitError, RPCError
from telethon.tl import types, functions
from tcli_skills import __version__


MEDIA_FILTERS = {"photo": types.InputMessagesFilterPhotos, "video": types.InputMessagesFilterVideo,
                 "document": types.InputMessagesFilterDocument, "audio": types.InputMessagesFilterMusic,
                 "voice": types.InputMessagesFilterVoice, "video_note": types.InputMessagesFilterRoundVideo,
                 "gif": types.InputMessagesFilterGif}


class CommandError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.error_code = code


class CLIParser(argparse.ArgumentParser):
    def error(self, message):
        arguments = sys.argv[1:]
        arguments = arguments[:arguments.index("--")] if "--" in arguments else arguments
        if "--json" in arguments or "--jsonl" in arguments:
            report_error(True, "usage_error", message)
            self.exit(2)
        super().error(message)


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Enter an integer greater than zero.")
    return number


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError("Use ISO 8601, for example 2026-10-20T10:00:00+03:00.") from None
    if result.tzinfo is None:
        if len(value) != 10:
            raise argparse.ArgumentTypeError("Include a timezone offset or Z in timestamps; dates mean midnight UTC.")
        result = result.replace(tzinfo=timezone.utc)
    return result


def schedule_timestamp(value):
    if len(value) == 10:
        raise argparse.ArgumentTypeError("Scheduling requires a timestamp with a time and timezone offset or Z.")
    return timestamp(value)


def history_arguments(command):
    command.add_argument("--after", type=positive_int, metavar="ID", help="message IDs greater than ID (exclusive)")
    command.add_argument("--since", type=timestamp, help="inclusive ISO date/time; dates mean midnight UTC")
    command.add_argument("--until", type=timestamp, help="exclusive ISO date/time; dates mean midnight UTC")
    command.add_argument("--from-user", help="sender: me, @username, or numeric user ID")
    command.add_argument("--topic", type=positive_int, metavar="ID", help="forum topic ID from topics")


def parser():
    cli = CLIParser(
        prog="tcli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''First time here?
  1. tcli config               Save API_ID and API_HASH from my.telegram.org
  2. tcli login                Sign in with your phone number
  3. tcli chats                Find a chat and its ID
  4. tcli read "Chat name"     Read recent messages

Examples:
  tcli send me "Hello!"        Send a message to Saved Messages
  tcli watch "Chat name"       Follow new messages (Ctrl+C to stop)
  tcli help send               Get help for a command

Use me, @username, an exact chat name, or a chat ID.
For negative IDs, put -- before the ID: tcli read -- -1001234567890
Help works without configuration or a Telegram login.''')
    cli.add_argument("--version", action="version", version=__version__)
    commands = cli.add_subparsers(dest="command", title="commands", metavar="COMMAND")
    commands.add_parser("capabilities", help="Describe commands, parameters, and output formats as JSON, offline")
    skills = commands.add_parser("skills", help="Install or update bundled agent skills without Telegram access")
    skill_commands = skills.add_subparsers(dest="skills_command", required=True)
    install = skill_commands.add_parser(
        "install", help="Install or update user skills, backing up changed files",
        description="Install into the official user skill directories: "
                    "Codex: ~/.agents/skills/telegram-cli/SKILL.md; "
                    "Claude Code: ~/.claude/skills/telegram-cli/SKILL.md. "
                    "By default, only detected agents are updated.")
    install.add_argument("--agent", choices=("auto", "all", "codex", "claude"), default="auto",
                         help="default: auto detects installed agents; explicit choices skip detection")
    commands.add_parser("config", help="Save API credentials locally",
                        description="Save or replace API_ID and API_HASH. No Telegram connection required.")
    commands.add_parser("login", help="Sign in to your personal account",
                        description="Sign in with your phone number, login code, and optional 2FA password.")
    commands.add_parser("logout", help="Revoke this CLI session",
                        description="Revoke this CLI session. API credentials are kept for your next login.")
    doctor = commands.add_parser("doctor", help="Check configuration and session without showing secrets")
    doctor.add_argument("--connect", action="store_true",
                        help="also check Telegram connectivity and authorization using the existing session")
    chats = commands.add_parser("chats", help="List chats and their IDs",
                                description="List chat IDs, names, and unread counts.")
    chats.add_argument("--limit", type=positive_int, default=50, metavar="N",
                       help="maximum number of chats (default: 50)")
    chats.add_argument("--json", action="store_true", help="output a JSON array")
    chats.add_argument("--search", help="case-insensitive substring of a chat title; limit counts matches")
    for name, help_text, example in (
            ("read", "Read recent messages, oldest first", 'tcli read "Chat name" --limit 10'),
            ("send", "Send text or a file with optional formatting", 'tcli send me --attach photo.jpg --caption "Hello!"'),
            ("edit", "Edit an outgoing message or attachment caption", 'tcli edit me 123 "Corrected text"'),
            ("download", "Download a message attachment to a new local file", 'tcli download me 123 --output photo.jpg'),
            ("forward", "Forward a message or attachment between chats", 'tcli forward "Source chat" 123 me --json'),
            ("watch", "Follow new messages until Ctrl+C", 'tcli watch -- -1001234567890'),
            ("info", "Show chat details and posting permissions", 'tcli info me --json'),
            ("pinned", "Read pinned messages", 'tcli pinned me --json'),
            ("topics", "List forum topics", 'tcli topics "Group" --json'),
            ("thread", "Follow a message reply chain towards its root", 'tcli thread me 123 --depth 10'),
            ("export", "Export selected history to a new Markdown file", 'tcli export me --output history.md'),
            ("scheduled", "List or cancel scheduled messages", 'tcli scheduled me --json')):
        command = commands.add_parser(name, help=help_text, description=help_text,
                                       epilog=f"Example: {example}")
        command.add_argument("chat", metavar="FROM_CHAT" if name == "forward" else "CHAT",
                             help="me, @username, exact chat name, or ID from tcli chats; use -- before negative IDs")
        command.add_argument("--json", action="store_true",
                             help="output JSON (watch: JSONL); errors are JSON on stderr")
        if name == "read":
            history_arguments(command)
            command.add_argument("--jsonl", action="store_true", help="stream matching history oldest first; limit counts oldest matches")
            command.add_argument("--limit", type=positive_int, default=20, metavar="N",
                                 help="number of recent messages (default: 20)")
            page = command.add_mutually_exclusive_group()
            page.add_argument("--id", type=positive_int, metavar="ID", help="read one specific message; no search or media filter")
            page.add_argument("--around", type=positive_int, metavar="ID", help="read a message and its surrounding conversation")
            page.add_argument("--before", type=positive_int, metavar="ID",
                                 help="read messages older than this message ID (exclusive)")
            command.add_argument("--search", metavar="QUERY", help="search message text using Telegram search")
            command.add_argument("--media", choices=MEDIA_FILTERS, help="filter attachments using Telegram search")
            command.add_argument("--filename", metavar="PATTERN", help="case-insensitive filename glob, e.g. *.apk")
            command.add_argument("--scan-limit", type=positive_int, metavar="N",
                                 help="maximum messages to inspect with --filename (default: 1000)")
            command.add_argument("--context", type=positive_int, metavar="N",
                                 help="messages on each side of --around (default: 5)")
        elif name in ("send", "edit"):
            if name == "edit":
                command.add_argument("message_id", type=positive_int, metavar="ID", help="outgoing message ID in this chat")
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument("text", nargs="?", metavar="TEXT", help="literal message text in quotes")
            source.add_argument("--stdin", action="store_true", help="read UTF-8 text from stdin until EOF")
            source.add_argument("--file", type=Path, metavar="PATH", help="read text from a UTF-8 file")
            if name == "send":
                command.add_argument("--silent", action="store_true", help="send without a notification sound")
                command.add_argument("--schedule", type=schedule_timestamp, help="future ISO timestamp with timezone; Telegram schedules delivery")
                source.add_argument("--attach", type=Path, nargs="+", metavar="PATH", help="upload one or more local files in order")
                command.add_argument("--caption", action="append", help="caption per attachment in order; repeat as needed")
                command.add_argument("--as-file", action="store_true", help="send as documents; requires --attach")
                command.add_argument("--album", action="store_true", help="group 2-10 compatible attachments as an album")
                command.add_argument("--reply-to", type=positive_int, metavar="ID", help="reply to a message in this chat")
            command.add_argument("--format", choices=("plain", "markdown", "html"), default="plain",
                                 help="text formatting (default: plain); uses Telethon Markdown or HTML")
        elif name == "download":
            command.add_argument("message_id", type=positive_int, nargs="?", metavar="ID", help="message ID; omit with --batch")
            command.add_argument("--batch", action="store_true", help="download attachments matching history filters")
            command.add_argument("--resume", action="store_true", help="keep verified .part files and resume interrupted downloads")
            command.add_argument("--max-bytes", type=positive_int, help="required with --batch: maximum total attachment bytes")
            command.add_argument("--limit", type=positive_int, default=20, help="maximum batch attachments (default: 20)")
            command.add_argument("--search", help="Telegram text search for batch downloads")
            command.add_argument("--media", choices=MEDIA_FILTERS, help="batch media filter")
            command.add_argument("--filename", help="batch filename glob, case-insensitive")
            command.add_argument("--scan-limit", type=positive_int, help="filename scan bound (default: 1000)")
            command.add_argument("--before", type=positive_int, help="batch messages older than ID")
            history_arguments(command)
            destination = command.add_mutually_exclusive_group(required=True)
            destination.add_argument("--output", type=Path, metavar="PATH",
                                 help="new destination file; parent directory must exist; never overwrite")
            destination.add_argument("--output-dir", type=Path, metavar="DIR", help="existing directory; requires --album or --batch")
            command.add_argument("--album", action="store_true", help="download all attachments in the selected message's album")
        elif name == "forward":
            command.add_argument("message_id", type=positive_int, metavar="ID", help="message ID in the source chat")
            command.add_argument("to_chat", metavar="TO_CHAT", help="destination chat selector")
        elif name == "export":
            history_arguments(command)
            command.add_argument("--output", type=Path, required=True, help="new Markdown file; never overwrite")
            command.add_argument("--limit", type=positive_int, default=1000, help="maximum messages (default: 1000)")
            command.add_argument("--before", type=positive_int)
            command.add_argument("--search")
            command.add_argument("--media", choices=MEDIA_FILTERS)
            command.add_argument("--filename")
            command.add_argument("--scan-limit", type=positive_int)
        elif name == "thread":
            command.add_argument("message_id", type=positive_int)
            command.add_argument("--depth", type=positive_int, default=10, help="maximum messages towards root (default: 10)")
        elif name == "topics":
            command.add_argument("--limit", type=positive_int, default=50)
            command.add_argument("--search", help="topic title search")
            command.add_argument("--before-topic", type=positive_int, help="continue before this topic from the previous page")
            command.add_argument("--offset-id", type=positive_int, help="top_message of the last topic on the previous page")
            command.add_argument("--offset-date", type=timestamp, help="offset_date of the last topic on the previous page")
        elif name == "pinned":
            command.add_argument("--limit", type=positive_int, default=20)
        elif name == "scheduled":
            command.add_argument("--cancel", type=positive_int, nargs="+", metavar="ID", help="cancel only these scheduled IDs in this chat")
        elif name == "watch":
            command.add_argument("--edits", action="store_true", help="detect edits via updates and polling the latest 100 messages; JSONL adds event=new|edit")
        if name in ("send", "download"):
            command.add_argument("--progress", action=argparse.BooleanOptionalAction, default=None,
                                 help="transfer progress on stderr (default: on in a terminal); JSON events with --json")
    help_command = commands.add_parser("help", help="Show general or command-specific help",
                                       description="Show help without connecting to Telegram.")
    help_command.add_argument("topic", nargs="?", metavar="COMMAND", help="command to explain, for example: send")
    return cli


def validate_credentials(api_id, api_hash):
    if (isinstance(api_id, bool) or not isinstance(api_id, (int, str)) or
            not re.fullmatch(r"[0-9]+", str(api_id)) or int(api_id) <= 0 or
            not isinstance(api_hash, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash)):
        raise ValueError("API_ID must be a positive integer and API_HASH must contain 32 hexadecimal characters. Run: tcli config")
    return int(api_id), api_hash


def load_credentials(path):
    if "TG_API_ID" in os.environ or "TG_API_HASH" in os.environ:
        return validate_credentials(os.environ.get("TG_API_ID"), os.environ.get("TG_API_HASH"))
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("API credentials are missing. Run: tcli config") from None
    except (ValueError, UnicodeError):
        raise ValueError("The configuration file is invalid. Run: tcli config") from None
    if not isinstance(config, dict):
        raise ValueError("The configuration file must contain a JSON object. Run: tcli config")
    return validate_credentials(config.get("api_id"), config.get("api_hash"))


def configure(path):
    api_id = input("API_ID from my.telegram.org: ").strip()
    api_hash = getpass("API_HASH (hidden input): ").strip()
    api_id, api_hash = validate_credentials(api_id, api_hash)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(path, (json.dumps({"api_id": api_id, "api_hash": api_hash}, indent=2) + "\n").encode("utf-8"))
    print(f"Configuration saved: {path}")
    if "TG_API_ID" in os.environ or "TG_API_HASH" in os.environ:
        print("Note: TG_API_ID and TG_API_HASH environment variables override the configuration file.")


def atomic_write(path, content):
    temporary = None
    try:
        # Replace only a complete file, so interrupted writes preserve the original.
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent,
                                         prefix=f".{path.stem}-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def check_update():
    if not sys.stdout.isatty() or os.environ.get("TCLI_NO_UPDATE_CHECK") == "1":
        return
    directory = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "telegram-cli" / "cache"
    path = directory / "update-check.json"
    now = time.time()
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}
    checked = cache.get("checked_at")
    if not isinstance(checked, (int, float)) or not 0 <= now - checked < 86400:
        latest = None
        try:
            request = Request("https://pypi.org/pypi/telegram-cli-local/json",
                              headers={"User-Agent": f"tcli/{__version__}"})
            with urlopen(request, timeout=1) as response:
                data = json.loads(response.read(262144))
            if isinstance(data, dict) and isinstance(data.get("info"), dict):
                latest = data["info"].get("version")
        except (OSError, ValueError, HTTPException):
            pass
        cache = {"checked_at": now, "latest": latest}
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_write(path, json.dumps(cache).encode("utf-8"))
        except OSError:
            pass
    latest = cache.get("latest")
    # shortcut: stable x.y.z releases only; use PEP 440 parsing if prereleases are introduced.
    if isinstance(latest, str) and re.fullmatch(r"[0-9]{1,8}\.[0-9]{1,8}\.[0-9]{1,8}", latest):
        if tuple(map(int, latest.split("."))) > tuple(map(int, __version__.split("."))):
            print(f"Update available: {__version__} -> {latest}. Run: pipx upgrade telegram-cli-local "
                  "(or python -m pip install --upgrade telegram-cli-local in your environment). "
                  "Then run: tcli skills install", file=sys.stderr)


def agent_installed(name):
    if shutil.which(name):
        return True
    home = Path.home()
    suffix = ".exe" if sys.platform == "win32" else ""
    candidates = [home / ".local" / "bin" / (name + suffix)]
    if sys.platform == "win32":
        candidates.append(Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "npm" / (name + ".cmd"))
    else:
        candidates.extend(Path(root) / name for root in ("/usr/local/bin", "/usr/bin", "/opt/homebrew/bin"))
    if any(path.is_file() and os.access(path, os.X_OK) for path in candidates):
        return True
    if name == "codex" and sys.platform == "darwin":
        return any((root / "Codex.app" / "Contents" / "MacOS" / "Codex").is_file()
                   for root in (Path("/Applications"), home / "Applications"))
    if name == "codex" and sys.platform == "win32":
        powershell = Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        try:
            result = subprocess.run(
                [str(powershell), "-NoProfile", "-NonInteractive", "-Command",
                 "Get-AppxPackage -Name OpenAI.Codex | Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return result.returncode == 0 and result.stdout.strip() == "OpenAI.Codex"
        except (OSError, subprocess.TimeoutExpired):
            return False
    return False


def install_skills(agent):
    content = files("tcli_skills").joinpath("SKILL.md").read_bytes()
    home = Path.home()
    targets = {"codex": home / ".agents" / "skills", "claude": home / ".claude" / "skills"}
    if agent == "auto":
        targets = {name: root for name, root in targets.items() if agent_installed(name)}
        if not targets:
            print("No supported agent installations found. Nothing installed. "
                  "For a custom installation, use --agent codex or --agent claude.")
            return
    for name, root in targets.items():
        if agent not in ("auto", "all", name):
            continue
        folder = root / "telegram-cli"
        path = folder / "SKILL.md"
        if folder.is_symlink() or path.is_symlink():
            raise ValueError(f"Skill is symlinked; update its source instead: {path}")
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.exists():
            previous = path.read_bytes()
            if previous == content:
                print(f"Skill already up to date ({name}): {path}")
                continue
            with tempfile.NamedTemporaryFile(mode="wb", dir=folder, prefix="SKILL.md.backup-",
                                             suffix=".bak", delete=False) as backup:
                backup.write(previous)
            print(f"Previous skill backed up: {backup.name}")
        atomic_write(path, content)
        print(f"Skill installed ({name}): {path}")
    print("If the updated skill does not appear, restart your agent or start a new session.")


def safe_text(value):
    # Remote messages must not execute terminal escape sequences or hide text with bidi controls.
    return "".join(c for c in str(value) if c in "\n\t" or
                   not unicodedata.category(c).startswith("C"))


def show_message(message):
    date = message.date.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    sender = "you" if message.out else str(message.sender_id or "?")
    name = sender_name(message)
    if name and not message.out:
        sender = f"{safe_text(name).replace(chr(10), ' ').replace(chr(9), ' ')} ({sender})"
    body = message.raw_text or "[media or service message]"
    media = media_data(message)
    if media is not None:
        details = [media["type"], media["name"], media["mime_type"]]
        if media["size"] is not None:
            details.append(f'{media["size"]} bytes')
        body = (message.raw_text + "\n" if message.raw_text else "") + "[" + ", ".join(filter(None, details)) + "]"
    context = ""
    if getattr(message, "reply_to_msg_id", None) is not None:
        context += f" [reply to #{message.reply_to_msg_id}]"
    if getattr(message, "grouped_id", None) is not None:
        context += f" [album {message.grouped_id}]"
    print(f"[{date}] {sender} (#{message.id}){context}: {safe_text(body)}", flush=True)


def media_data(message):
    file = getattr(message, "file", None)
    if file is None:
        return None
    kind = next((name for name in ("photo", "voice", "video_note", "gif", "video", "audio", "sticker")
                 if getattr(message, name, None)), "document")
    return {"type": kind, "name": file.name, "mime_type": file.mime_type,
            "size": file.size, "ext": file.ext, "width": file.width,
            "height": file.height, "duration": file.duration}


def message_data(message):
    return {"id": message.id, "chat_id": message.chat_id,
            "date": message.date.isoformat(), "sender_id": message.sender_id,
            "out": message.out, "text": message.raw_text or "",
            "has_media": bool(message.media), "media": media_data(message),
            "reply_to_id": getattr(message, "reply_to_msg_id", None),
            "grouped_id": getattr(message, "grouped_id", None),
            "sender_name": sender_name(message)}


def sender_name(message):
    sender = getattr(message, "sender", None)
    return (utils.get_display_name(sender) or None) if sender is not None else None


def message_link(message):
    chat = getattr(message, "chat", None)
    if isinstance(chat, types.Channel) and chat.username:
        return f"https://t.me/{chat.username}/{message.id}"
    if message.chat_id and message.chat_id < -1000000000000:
        return f"https://t.me/c/{-message.chat_id - 1000000000000}/{message.id}"
    return None


def parse_message_link(value):
    if not value.startswith(("https://", "http://", "t.me/")):
        return None
    url = urlsplit(value if "://" in value else "https://" + value)
    if url.scheme != "https" or url.netloc.lower() not in ("t.me", "www.t.me") or url.query or url.fragment:
        raise ValueError("Use an https://t.me/username/ID or https://t.me/c/channel/ID message link.")
    parts = url.path.strip("/").split("/")
    if len(parts) == 3 and parts[0] == "c" and all(re.fullmatch(r"[1-9][0-9]*", p) for p in parts[1:]):
        return str(-1000000000000 - int(parts[1])), int(parts[2])
    if len(parts) == 2 and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", parts[0]) and re.fullmatch(r"[1-9][0-9]*", parts[1]):
        return "@" + parts[0], int(parts[1])
    raise ValueError("Unsupported message link; use https://t.me/username/ID or https://t.me/c/channel/ID.")


def capabilities(cli):
    def describe(command):
        result = {"description": command.description, "parameters": [], "commands": {}}
        for action in command._actions:
            if isinstance(action, argparse._SubParsersAction):
                result["commands"] = {key: describe(sub) for key, sub in action.choices.items()}
            elif action.dest not in ("help", "version"):
                result["parameters"].append({"name": action.dest, "flags": action.option_strings,
                    "required": action.required, "default": action.default,
                    "choices": list(action.choices) if action.choices is not None else None,
                    "nargs": action.nargs, "help": action.help,
                    "type": "boolean" if isinstance(action, (argparse._StoreTrueAction, argparse.BooleanOptionalAction))
                            else getattr(action.type, "__name__", "string")})
        return result
    return {"version": __version__, "commands": describe(cli)["commands"],
            "outputs": {"chats": "array", "read": "array (--json) or JSONL (--jsonl)",
                        "watch": "JSONL; --edits adds event=new|edit", "info": "object",
                        "pinned": "array", "topics": "array", "thread": "array", "scheduled": "array or cancellation result",
                        "send": "{ok,messages}", "edit": "{ok,message}", "forward": "{ok,messages}",
                        "download": "{ok,files}", "export": "{ok,path,count}", "errors": "stderr: {ok:false,error}"},
            "constraints": ["Explicit permission is needed for sends, edits, forwards, scheduling and cancellation.",
                            "JSONL can be partial on failure; check exit status.",
                            "Topic reads cannot combine with text/media/sender filters."]}


async def history_options(client, args):
    options = {}
    for source, target in (("before", "offset_id"), ("after", "min_id"), ("search", "search"), ("until", "offset_date"), ("topic", "reply_to")):
        value = getattr(args, source, None)
        if value is not None:
            options[target] = value
    if args.topic == 1:
        options.pop("reply_to", None)
    if args.media is not None:
        options["filter"] = MEDIA_FILTERS[args.media]()
    if args.from_user is not None:
        value = args.from_user
        options["from_user"] = await client.get_input_entity(int(value) if re.fullmatch(r"-?\d+", value) else value)
    return options


def filename_matches(message, pattern):
    return (message.file is not None and message.file.name is not None
            and fnmatchcase(message.file.name.casefold(), pattern.casefold()))


def scan_notice(args, scanned, matched, last_id=None, *, ascending=False):
    limit = args.scan_limit or 1000
    if scanned < limit or matched >= args.limit:
        return
    direction = "after" if ascending else "before"
    data = {"event": "scan_limit_reached", "scanned": scanned, "scan_limit": limit,
            f"next_{direction}": last_id,
            "message": f"Filename search is incomplete: scan limit reached. Continue with --{direction} {last_id} or increase --scan-limit."}
    if args.json:
        print(json.dumps(data), file=sys.stderr)
    else:
        print(data["message"], file=sys.stderr)


async def iter_history(client, chat, args, *, ascending=False):
    options = await history_options(client, args)
    if ascending:
        # Keep date bounds explicit: Telethon reverses offset_date when reverse=True.
        options.pop("offset_date", None)
        if "offset_id" in options:
            options["max_id"] = options.pop("offset_id")
    scanned = matched = 0
    last_id = None
    scan_limit = (args.scan_limit or 1000) if args.filename else None
    iterator = client.iter_messages(chat, limit=None, reverse=ascending, **options)
    async for message in iterator:
        if args.since is not None and message.date < args.since:
            if not ascending:
                break
            continue
        if args.until is not None and message.date >= args.until:
            if ascending:
                break
            continue
        scanned += 1
        last_id = message.id
        matches = not args.filename or filename_matches(message, args.filename)
        if args.topic == 1:
            header = getattr(message, "reply_to", None)
            if getattr(header, "forum_topic", False):
                topic_id = getattr(header, "reply_to_top_id", None) or getattr(header, "reply_to_msg_id", None)
                matches = matches and topic_id == 1
            elif isinstance(getattr(message, "action", None), types.MessageActionTopicCreate):
                matches = False
        if getattr(args, "batch", False) and message.file is None:
            matches = False
        if matches:
            matched += 1
            yield message
            if matched >= args.limit:
                break
        if scan_limit is not None and scanned >= scan_limit:
            break
    if args.filename:
        scan_notice(args, scanned, matched, last_id, ascending=ascending)


async def chat_info(client, chat):
    entity = await client.get_entity(chat)
    if isinstance(entity, types.User):
        response = await client(functions.users.GetFullUserRequest(entity))
        full = response.full_user
        kind = "user"
    elif isinstance(entity, types.Channel):
        response = await client(functions.channels.GetFullChannelRequest(entity))
        full = response.full_chat
        kind = "group" if entity.megagroup else "channel"
    else:
        response = await client(functions.messages.GetFullChatRequest(entity.id))
        full = response.full_chat
        kind = "group"
    permissions = await client.get_permissions(chat, "me") if kind != "user" else None
    rights = None
    if permissions is not None:
        rights = {key: bool(getattr(permissions, key, False)) for key in
                  ("is_creator", "is_admin", "has_left", "is_banned", "post_messages", "pin_messages", "delete_messages")}
        restricted = [getattr(entity, "default_banned_rights", None), getattr(entity, "banned_rights", None),
                      getattr(getattr(permissions, "participant", None), "banned_rights", None)]
        if kind == "channel":
            rights["send_messages"] = rights["is_creator"] or rights["post_messages"]
            rights["send_media"] = rights["send_messages"]
        else:
            member = not rights["has_left"] and not getattr(entity, "left", False)
            rights["send_messages"] = member and (rights["is_admin"] or not any(
                getattr(item, key, False) for item in restricted for key in ("view_messages", "send_messages", "send_plain")))
            rights["send_media"] = rights["send_messages"] and (rights["is_admin"] or not any(
                getattr(item, "send_media", False) for item in restricted))
    return {"id": utils.get_peer_id(entity), "name": utils.get_display_name(entity), "type": kind,
            "username": getattr(entity, "username", None), "description": getattr(full, "about", None),
            "forum": bool(getattr(entity, "forum", False)),
            "permissions": rights}


async def reply_chain(client, chat, message_id, depth):
    messages, seen = [], set()
    status = "root"
    while message_id and len(messages) < depth:
        if message_id in seen:
            status = "cycle"
            break
        seen.add(message_id)
        message = await client.get_messages(chat, ids=message_id)
        if message is None:
            if not messages:
                raise CommandError("message_not_found", "Message not found in this chat.")
            status = "missing_parent"
            break
        messages.append(message)
        header = getattr(message, "reply_to", None)
        peer = getattr(header, "reply_to_peer_id", None)
        if peer is not None and utils.get_peer_id(peer) != message.chat_id:
            status = "cross_chat_parent"
            break
        message_id = message.reply_to_msg_id
    else:
        if message_id:
            status = "depth_limit"
    if status != "root":
        notice = {"event": "thread_incomplete", "reason": status}
        print(json.dumps(notice), file=sys.stderr)
    return list(reversed(messages))


def markdown_message(message):
    name = sender_name(message) or ("you" if message.out else str(message.sender_id or "?"))
    link = message_link(message)
    heading = f"{message.date.isoformat()} · {name.replace(chr(10), ' ')} · #{message.id}"
    media = media_data(message)
    text = safe_text(message.raw_text or "")
    if media:
        text += f"\n[Attachment: {media['type']}; name={media['name']}; bytes={media['size']}]"
    # Quote untrusted message bodies so embedded Markdown cannot inject document sections.
    body = "\n".join("> " + line for line in text.splitlines())
    return f"## {safe_text(heading)}\n\n" + (f"[Open message]({link})\n\n" if link else "") + body + "\n\n"


def report_error(json_mode, code, message, *, retry_after=None, args=None, **details):
    if json_mode:
        result = {"ok": False, "error": {"code": code, "message": str(message),
                                         "retry_after": retry_after, **details}}
        if args is not None and hasattr(args, "sent_messages"):
            result["messages"] = [message_data(item) for item in args.sent_messages]
        if args is not None and hasattr(args, "downloaded_files"):
            result["files"] = args.downloaded_files
        print(json.dumps(result), file=sys.stderr, flush=True)
    else:
        print(f"Error: {safe_text(message)}", file=sys.stderr)


async def album_messages(client, chat, message):
    if message.grouped_id is None:
        raise CommandError("not_an_album", "This message is not part of an album.")
    # Telegram albums contain at most ten adjacent messages; IDs can have gaps within a chat.
    older = await client.get_messages(chat, limit=10, offset_id=message.id)
    newer = await client.get_messages(chat, limit=10, min_id=message.id, reverse=True)
    return sorted((item for item in [*older, message, *newer]
                   if getattr(item, "grouped_id", None) == message.grouped_id), key=lambda item: item.id)


def transfer_progress(args, operation, label, sizes=None):
    if args.progress is False or (args.progress is None and not sys.stderr.isatty()):
        return None
    started = time.monotonic()
    last = None

    def update(current, total):
        nonlocal last
        if sizes is not None:
            completed = min(int(current), len(sizes))
            current = sum(sizes[:completed]) + (sizes[completed] * (current - completed) if completed < len(sizes) else 0)
            total = sum(sizes)
        now = time.monotonic()
        if last is not None and now - last < 0.5 and current < total:
            return
        last = now
        speed = current / max(now - started, 0.000001)
        percent = min(100, 100 * current / total) if total > 0 else None
        if args.json:
            print(json.dumps({"event": "transfer_progress", "operation": operation, "file": str(label),
                              "transferred_bytes": int(current), "total_bytes": total,
                              "percent": percent, "bytes_per_second": speed}), file=sys.stderr, flush=True)
        else:
            percentage = f"{percent:.1f}%" if percent is not None else "unknown size"
            print(f"{operation.title()} {safe_text(label)}: {int(current)}/{total} bytes "
                  f"({percentage}), {speed:.0f} bytes/s", file=sys.stderr, flush=True)

    return update


async def download_attachment(client, message, path, progress_callback=None):
    if message.file is None:
        raise CommandError("no_attachment", "This message has no downloadable attachment.")
    with path.open("xb") as stream:
        try:
            options = {"progress_callback": progress_callback} if progress_callback is not None else {}
            result = await client.download_media(message, file=stream, **options)
            if result is None:
                raise CommandError("no_attachment", "This attachment cannot be downloaded.")
        except BaseException:
            stream.close()
            path.unlink(missing_ok=True)
            raise
    return {"message_id": message.id, "chat_id": message.chat_id, "path": str(path.resolve()),
            "text": message.raw_text or "", "grouped_id": getattr(message, "grouped_id", None)}


def partial_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


async def resume_attachment(client, message, path, progress_callback=None):
    if message.file is None or message.file.size is None:
        raise CommandError("no_attachment", "Resuming requires a downloadable attachment with a known size.")
    media = getattr(message, "document", None) or getattr(message, "photo", None)
    if media is None:
        raise ValueError("This attachment cannot be resumed.")
    identity = {"chat_id": message.chat_id, "message_id": message.id,
                "media_id": media.id, "media_type": type(media).__name__, "size": message.file.size}
    part = path.with_name(path.name + ".part")
    manifest = path.with_name(path.name + ".part.json")
    lock = path.with_name(path.name + ".part.lock")
    for candidate in (path, part, manifest, lock):
        if candidate.is_symlink():
            raise ValueError("Resume paths must not be symlinks.")
    if os.path.lexists(path):
        raise FileExistsError(f"Destination already exists: {path}")
    try:
        guard = lock.open("x")
    except FileExistsError:
        raise CommandError("download_locked", f"Another download may be using {path}. Stop it before removing {lock}.") from None
    try:
        with guard:
            guard.write(str(os.getpid()))
        if part.exists() or manifest.exists():
            if not part.is_file() or not manifest.is_file():
                raise ValueError("Both .part and .part.json are required to resume; choose another output or remove both.")
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or saved.get("identity") != identity or saved.get("sha256") != partial_digest(part):
                raise ValueError("Partial download does not match the attachment or its checksum; refusing to append.")
        else:
            with part.open("xb"):
                pass
            atomic_write(manifest, json.dumps({"identity": identity, "sha256": partial_digest(part)}).encode())
        offset = part.stat().st_size
        if offset > message.file.size:
            raise ValueError("Partial file is larger than the attachment.")
        try:
            with part.open("ab") as stream:
                if offset < message.file.size:
                    iterator = client.iter_download(message.media, offset=offset, file_size=message.file.size)
                    try:
                        async for block in iterator:
                            if offset + len(block) > message.file.size:
                                raise ValueError("Download exceeded the declared attachment size.")
                            stream.write(block)
                            offset += len(block)
                            if progress_callback:
                                progress_callback(offset, message.file.size)
                    finally:
                        await iterator.close()
            if offset != message.file.size:
                raise OSError("Download ended before the declared attachment size; partial file retained.")
        finally:
            atomic_write(manifest, json.dumps({"identity": identity, "sha256": partial_digest(part)}).encode())
        # Hard-link publication is exclusive and atomic; it never replaces an existing destination.
        os.link(part, path)
        part.unlink()
        manifest.unlink()
    finally:
        lock.unlink(missing_ok=True)
    return {"message_id": message.id, "chat_id": message.chat_id, "path": str(path.resolve()),
            "text": message.raw_text or "", "grouped_id": getattr(message, "grouped_id", None)}


async def resolve_chat(client, value):
    if value == "me" or value.startswith("@"):
        return await client.get_input_entity(value)
    numeric = re.fullmatch(r"-?\d+", value)
    matches = []
    async for dialog in client.iter_dialogs():
        if (numeric and dialog.id == int(value)) or (not numeric and dialog.name == value):
            if numeric:
                return dialog.input_entity
            matches.append(dialog.input_entity)
    if not matches:
        raise CommandError("chat_not_found", "Chat not found. Use an ID from tcli chats or an @username.")
    if len(matches) > 1:
        raise CommandError("ambiguous_chat", "Multiple chats have this name. Use an ID from tcli chats.")
    return matches[0]


async def run(client, args):
    try:
        await client.connect()
        if args.command == "login":
            phone = input("Phone number with country code: ").strip()
            if not re.fullmatch(r"\+\d{7,15}", phone):
                raise ValueError("Enter an international phone number, for example +15551234567.")
            await client.start(phone=phone,
                               code_callback=lambda: getpass("Telegram login code: "),
                               password=lambda: getpass("Two-factor authentication password: "))
            me = await client.get_me()
            if me.bot:
                await client.log_out()
                raise ValueError("A personal Telegram account is required; bot accounts are not supported.")
            print(f"Signed in as {safe_text(me.first_name)} (ID {me.id}).")
            return
        if not await client.is_user_authorized():
            raise CommandError("unauthorized", "You are not signed in. Run: tcli login")
        if args.command == "doctor":
            print("Telegram connection: OK\nSession authorization: OK")
        elif args.command == "logout":
            await client.log_out()
            print("CLI session revoked.")
        elif args.command == "chats":
            dialogs = []
            matched = 0
            async for dialog in client.iter_dialogs(limit=None if args.search else args.limit):
                if args.search and args.search.casefold() not in (dialog.name or "").casefold():
                    continue
                if args.json:
                    dialogs.append({"id": dialog.id, "name": dialog.name, "unread_count": dialog.unread_count})
                else:
                    name = safe_text(dialog.name).replace("\n", " ").replace("\t", " ")
                    print(f"{dialog.id}\t{name}\tunread: {dialog.unread_count}")
                matched += 1
                if matched >= args.limit:
                    break
            if args.json:
                print(json.dumps(dialogs))
        else:
            chat = await resolve_chat(client, args.chat)
            if getattr(args, "topic", None) is not None:
                entity = await client.get_entity(chat)
                if not getattr(entity, "forum", False):
                    raise ValueError("--topic requires a forum chat.")
            if args.command == "read":
                if args.jsonl:
                    async for message in iter_history(client, chat, args, ascending=True):
                        print(json.dumps(message_data(message)), flush=True)
                    return
                options = await history_options(client, args)
                if args.id is not None or args.around is not None:
                    message = await client.get_messages(chat, ids=args.id or args.around)
                    if message is None:
                        raise CommandError("message_not_found", "Message not found in this chat.")
                    messages = [message]
                    if args.around is not None:
                        context = args.context or 5
                        older = await client.get_messages(chat, limit=context, offset_id=message.id)
                        newer = await client.get_messages(chat, limit=context, min_id=message.id, reverse=True)
                        messages = sorted([*older, message, *newer], key=lambda item: item.id, reverse=True)
                elif args.filename is not None:
                    if args.since is not None or args.until is not None or args.from_user is not None or args.after is not None or args.topic is not None:
                        messages = [message async for message in iter_history(client, chat, args)]
                    else:
                        scanned = await client.get_messages(chat, limit=args.scan_limit or 1000, **options)
                        messages = [message for message in scanned if filename_matches(message, args.filename)][:args.limit]
                        scan_notice(args, len(scanned), len(messages), scanned[-1].id if scanned else None)
                elif args.since is not None or args.until is not None or args.topic == 1:
                    messages = [message async for message in iter_history(client, chat, args)]
                else:
                    messages = await client.get_messages(chat, limit=args.limit, **options)
                if args.json:
                    print(json.dumps([message_data(message) for message in reversed(messages)]))
                else:
                    for message in reversed(messages):
                        show_message(message)
            elif args.command == "send":
                if args.attach is None and not args.text.strip():
                    raise ValueError("Message text must not be empty.")
                options = {}
                if args.silent:
                    options["silent"] = True
                if args.schedule is not None:
                    options["schedule"] = args.schedule
                if args.reply_to is not None:
                    options["reply_to"] = args.reply_to
                mode = None if args.format == "plain" else args.format
                args.sent_messages = []
                if args.attach is not None:
                    captions = (args.caption or []) + [""] * (len(args.attach) - len(args.caption or []))
                    if args.album:
                        progress = transfer_progress(args, "upload", "album", [path.stat().st_size for path in args.attach])
                        if progress is not None:
                            options["progress_callback"] = progress
                        with ExitStack() as stack:
                            streams = [stack.enter_context(path.open("rb")) for path in args.attach]
                            messages = await client.send_file(chat, streams, caption=captions,
                                                              force_document=args.as_file, parse_mode=mode, **options)
                        args.sent_messages.extend(messages)
                        if not args.json:
                            for message in messages:
                                print(f"Message {'scheduled' if args.schedule else 'sent'}. ID: {message.id}")
                    else:
                        for path, caption in zip(args.attach, captions):
                            progress = transfer_progress(args, "upload", path)
                            if progress is not None:
                                options["progress_callback"] = progress
                            with path.open("rb") as stream:
                                message = await client.send_file(chat, stream, caption=caption,
                                                                 force_document=args.as_file, parse_mode=mode, **options)
                            args.sent_messages.append(message)
                            if not args.json:
                                print(f"Message {'scheduled' if args.schedule else 'sent'}. ID: {message.id}")
                else:
                    message = await client.send_message(chat, args.text, parse_mode=mode, **options)
                    args.sent_messages.append(message)
                    if not args.json:
                        print(f"Message {'scheduled' if args.schedule else 'sent'}. ID: {message.id}")
                if args.json:
                    print(json.dumps({"ok": True, "messages": [message_data(item) for item in args.sent_messages]}))
            elif args.command == "forward":
                destination = await resolve_chat(client, args.to_chat)
                args.sent_messages = []
                message = await client.forward_messages(destination, args.message_id, from_peer=chat)
                if message is None:
                    raise CommandError("message_not_found", "Message not found or cannot be forwarded.")
                args.sent_messages.append(message)
                if args.json:
                    print(json.dumps({"ok": True, "messages": [message_data(message)]}))
                else:
                    print(f"Message forwarded. ID: {message.id}")
            elif args.command == "edit":
                original = await client.get_messages(chat, ids=args.message_id)
                if original is None:
                    raise CommandError("message_not_found", "Message not found in this chat.")
                if not original.out:
                    raise CommandError("not_outgoing", "Only outgoing messages can be edited.")
                mode = None if args.format == "plain" else args.format
                message = await client.edit_message(chat, args.message_id, args.text, parse_mode=mode)
                if message is None:
                    raise CommandError("edit_failed", "Telegram did not return the edited message.")
                if args.json:
                    print(json.dumps({"ok": True, "message": message_data(message)}))
                else:
                    print(f"Message edited. ID: {message.id}")
            elif args.command == "download":
                if args.batch:
                    messages = []
                    total = 0
                    async for item in iter_history(client, chat, args):
                        if item.file is None:
                            continue
                        if item.file.size is None:
                            raise ValueError("Cannot enforce --max-bytes for an attachment with unknown size.")
                        total += item.file.size
                        if total > args.max_bytes:
                            raise ValueError("Selected attachments exceed --max-bytes; reduce --limit or narrow filters. Nothing downloaded.")
                        messages.append(item)
                else:
                    message = await client.get_messages(chat, ids=args.message_id)
                    if message is None:
                        raise CommandError("message_not_found", "Message not found in this chat.")
                    messages = await album_messages(client, chat, message) if args.album else [message]
                targets = []
                for item in messages:
                    if item.file is None:
                        raise CommandError("no_attachment", "This message has no downloadable attachment.")
                    extension = item.file.ext or ".bin"
                    if not re.fullmatch(r"\.[a-zA-Z0-9]{1,12}", extension):
                        extension = ".bin"
                    path = args.output_dir / f"{item.id}{extension}" if args.album or args.batch else args.output
                    if os.path.lexists(path):
                        raise FileExistsError(f"Destination already exists: {path}")
                    targets.append((item, path))
                args.downloaded_files = []
                for item, path in targets:
                    download = resume_attachment if args.resume else download_attachment
                    args.downloaded_files.append(await download(
                        client, item, path, transfer_progress(args, "download", path)))
                    if not args.json:
                        print(f"Downloaded: {safe_text(path.resolve())}")
                if args.json:
                    print(json.dumps({"ok": True, "files": args.downloaded_files}))
            elif args.command == "info":
                data = await chat_info(client, chat)
                if args.json:
                    print(json.dumps(data))
                else:
                    for key, value in data.items():
                        print(f"{key}: {safe_text(value)}")
            elif args.command in ("pinned", "thread", "scheduled"):
                if args.command == "pinned":
                    messages = list(reversed(await client.get_messages(chat, limit=args.limit, filter=types.InputMessagesFilterPinned())))
                elif args.command == "thread":
                    messages = await reply_chain(client, chat, args.message_id, args.depth)
                else:
                    messages = await client.get_messages(chat, scheduled=True, limit=None)
                    if args.cancel:
                        if not set(args.cancel).issubset({item.id for item in messages}):
                            raise CommandError("message_not_found", "A scheduled ID was not found in this chat; nothing cancelled.")
                        await client(functions.messages.DeleteScheduledMessagesRequest(chat, id=args.cancel))
                        if args.json:
                            print(json.dumps({"ok": True, "cancelled_ids": args.cancel}))
                        else:
                            print("Scheduled messages cancelled: " + ", ".join(map(str, args.cancel)))
                        return
                    messages = sorted(messages, key=lambda item: item.date)
                if args.json:
                    print(json.dumps([message_data(item) for item in messages]))
                else:
                    for message in messages:
                        show_message(message)
            elif args.command == "topics":
                entity = await client.get_entity(chat)
                if not getattr(entity, "forum", False):
                    raise ValueError("This chat is not a forum.")
                result = await client(functions.messages.GetForumTopicsRequest(
                    peer=chat, offset_date=args.offset_date, offset_id=args.offset_id or 0,
                    offset_topic=args.before_topic or 0, limit=args.limit, q=args.search))
                dates = {item.id: getattr(item, "date", None) for item in result.messages}
                topics = []
                for item in result.topics:
                    if not isinstance(item, types.ForumTopic):
                        continue
                    offset = item.date if result.order_by_create_date else dates.get(item.top_message)
                    topics.append({"id": item.id, "title": item.title, "top_message": item.top_message,
                                   "date": item.date.isoformat() if item.date else None,
                                   "offset_date": offset.isoformat() if offset else None,
                                   "closed": item.closed, "pinned": item.pinned, "unread_count": item.unread_count})
                if args.json:
                    print(json.dumps(topics))
                else:
                    for item in topics:
                        print(f"{item['id']}\t{safe_text(item['title'])}\tunread: {item['unread_count']}")
            elif args.command == "export":
                messages = [message async for message in iter_history(client, chat, args)]
                with args.output.open("x", encoding="utf-8") as stream:
                    try:
                        stream.write("# Telegram history\n\n")
                        for message in reversed(messages):
                            stream.write(markdown_message(message))
                    except BaseException:
                        stream.close()
                        args.output.unlink(missing_ok=True)
                        raise
                if args.json:
                    print(json.dumps({"ok": True, "path": str(args.output.resolve()), "count": len(messages)}))
                else:
                    print(f"Exported {len(messages)} messages: {safe_text(args.output.resolve())}")
            elif args.command == "watch":
                def revision(message):
                    media = getattr(message, "document", None) or getattr(message, "photo", None)
                    return (getattr(message, "edit_date", None), message.raw_text,
                            getattr(message, "entities", None), getattr(media, "id", None))

                def show_edit(message):
                    current = revision(message)
                    if revisions.get(message.id) == current:
                        return
                    if message.id in revisions:
                        revisions[message.id] = current
                    if args.json:
                        print(json.dumps({**message_data(message), "event": "edit"}), flush=True)
                    else:
                        print("[edited]", end=" ")
                        show_message(message)

                async def edited(event):
                    show_edit(event.message)
                    client.session.save()
                builder = events.MessageEdited(chats=chat)
                latest = await client.get_messages(chat, limit=100 if args.edits else 1)
                revisions = {message.id: revision(message) for message in latest} if args.edits else {}
                last_id = latest[0].id if latest else 0
                client.session.save()
                print("Watching for new messages. Press Ctrl+C to stop.",
                      file=sys.stderr if args.json else sys.stdout, flush=True)
                # Shared authorizations do not reliably receive each other's outgoing updates.
                if args.edits:
                    client.add_event_handler(edited, builder)
                try:
                    while client.is_connected():
                        if args.edits:
                            # shortcut: poll edits in the latest 100 messages; older edits require Telegram updates.
                            recent = await client.get_messages(chat, limit=100)
                            messages = list(reversed(recent))
                            if messages and messages[0].id > last_id:
                                missed = await client.get_messages(chat, min_id=last_id, max_id=messages[0].id,
                                                                   reverse=True, limit=None)
                                messages = [*missed, *messages]
                        else:
                            messages = await client.get_messages(chat, min_id=last_id, reverse=True, limit=None)
                        # Commit entity-cache writes before sleeping so other CLI processes can use SQLite.
                        client.session.save()
                        for message in messages:
                            if args.edits and message.id <= last_id:
                                if message.id in revisions:
                                    show_edit(message)
                                continue
                            if args.json:
                                data = message_data(message)
                                if args.edits:
                                    data["event"] = "new"
                                print(json.dumps(data), flush=True)
                            else:
                                show_message(message)
                            last_id = message.id
                        if args.edits:
                            revisions = {message.id: revision(message) for message in messages[-100:]}
                        await asyncio.sleep(2)
                finally:
                    if args.edits:
                        client.remove_event_handler(edited, builder)
    finally:
        await client.disconnect()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    cli = parser()
    args = cli.parse_args([] if sys.argv[1:] in (["--help"], ["-h"]) else None)
    if args.command is None or args.command == "help":
        if args.command == "help" and args.topic:
            cli.parse_args([args.topic, "--help"])
        cli.print_help()
        check_update()
        return 0
    try:
        if args.command == "read" and args.jsonl:
            if args.json:
                raise ValueError("Choose --json or --jsonl.")
            args.json = True
        if args.command == "capabilities":
            print(json.dumps(capabilities(cli)))
            return 0
        if args.command == "skills":
            install_skills(args.agent)
            return 0
        session_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "telegram-cli"
        config_path = session_dir / "config.json"
        if args.command == "config":
            configure(config_path)
            return 0
        if hasattr(args, "chat"):
            link = parse_message_link(args.chat)
            if link:
                if args.command != "read" or args.id is not None or args.around is not None or args.before is not None:
                    raise ValueError("Message links are supported by read without --id, --around or --before.")
                args.chat, args.id = link
        if args.command in ("read", "export", "download"):
            if args.since is not None and args.until is not None and args.since >= args.until:
                raise ValueError("--since must be earlier than --until.")
            if args.after is not None and args.before is not None and args.after >= args.before:
                raise ValueError("--after must be smaller than --before.")
            if args.topic is not None and any(value is not None for value in (args.search, args.media, args.from_user)):
                raise ValueError("--topic cannot combine with --search, --media or --from-user (Telegram ignores these filters for replies).")
            if args.scan_limit is not None and args.filename is None:
                raise ValueError("--scan-limit requires --filename.")
            if args.filename is not None and not args.filename.strip():
                raise ValueError("Filename pattern must not be empty.")
        if args.command == "export":
            if not args.output.parent.is_dir():
                raise ValueError("The destination directory must exist.")
            if os.path.lexists(args.output):
                raise FileExistsError(f"Destination already exists: {args.output}")
        if args.command == "topics":
            offsets = (args.before_topic, args.offset_id, args.offset_date)
            if any(value is not None for value in offsets) and not all(value is not None for value in offsets):
                raise ValueError("Topic pagination requires --before-topic, --offset-id and --offset-date together.")
        if args.command == "read":
            if (args.id is not None or args.around is not None) and any(
                    value is not None for value in (args.search, args.media, args.filename, args.scan_limit,
                                                    args.after, args.since, args.until, args.from_user, args.topic)):
                raise ValueError("--id and --around cannot be combined with search or media/filename filters.")
            if args.jsonl and (args.id is not None or args.around is not None):
                raise ValueError("--jsonl streams history; use --json for --id or --around.")
            if args.context is not None and args.around is None:
                raise ValueError("--context requires --around.")
        if args.command == "send" and args.attach is None and args.progress is not None:
            raise ValueError("--progress and --no-progress require --attach.")
        if args.command == "download":
            if args.batch:
                if args.message_id is not None or args.album or args.output_dir is None or args.max_bytes is None:
                    raise ValueError("--batch requires --output-dir and --max-bytes, without a message ID or --album.")
            else:
                if args.message_id is None:
                    raise ValueError("Provide a message ID or --batch.")
                if any(value is not None for value in (args.search, args.media, args.filename, args.scan_limit,
                                                       args.before, args.after, args.since, args.until, args.from_user, args.topic, args.max_bytes)):
                    raise ValueError("History filters and --max-bytes require --batch.")
            if (args.album or args.batch) != (args.output_dir is not None):
                raise ValueError("--album/--batch require --output-dir; --output-dir requires --album or --batch.")
            parent = args.output_dir if args.output_dir is not None else args.output.parent
            if not parent.is_dir():
                raise ValueError("The destination directory must exist.")
            if args.output is not None and os.path.lexists(args.output):
                raise FileExistsError(f"Destination already exists: {args.output}")
        if args.command in ("send", "edit"):
            if args.command == "send":
                if args.schedule is not None and args.schedule <= datetime.now(timezone.utc):
                    raise ValueError("--schedule must be a future timestamp with a timezone.")
                if args.attach is None and (args.caption is not None or args.as_file or args.album):
                    raise ValueError("--caption, --as-file and --album require --attach.")
                if args.attach is not None:
                    if any(not path.is_file() for path in args.attach):
                        raise ValueError("Every attachment must be an existing local file.")
                    if len(args.caption or []) > len(args.attach):
                        raise ValueError("Provide at most one --caption per attachment.")
                    if args.album and not 2 <= len(args.attach) <= 10:
                        raise ValueError("An album requires 2-10 compatible attachments.")
            if args.stdin:
                sys.stdin.reconfigure(encoding="utf-8-sig")
                args.text = sys.stdin.read()
            elif args.file is not None:
                args.text = args.file.read_text(encoding="utf-8-sig")
            if (args.command == "edit" or args.attach is None) and not args.text.strip():
                raise ValueError("Message text must not be empty.")
        if args.command == "doctor":
            print(f"Configuration path: {safe_text(config_path)}")
            print(f"Session path: {safe_text(session_dir / 'account.session')}")
        try:
            api_id, api_hash = load_credentials(config_path)
        except ValueError as error:
            raise CommandError("configuration_error", str(error)) from error
        if args.command == "doctor":
            print("API credentials: valid (values hidden)")
            if not (session_dir / "account.session").is_file():
                raise ValueError("Session file is missing. Run: tcli login")
            print("Session file: present (authorization not checked locally)")
            if not args.connect:
                print("Use tcli doctor --connect to check Telegram connectivity and authorization.")
                return 0
        session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        client = TelegramClient(str(session_dir / "account"), api_id, api_hash,
                                flood_sleep_threshold=0)
        asyncio.run(run(client, args))
    except KeyboardInterrupt:
        if getattr(args, "json", False):
            report_error(True, "cancelled", "Operation interrupted.", args=args)
        return 130
    except FloodWaitError as error:
        report_error(getattr(args, "json", False), "flood_wait",
                     f"Telegram rate limit: wait {error.seconds} seconds before trying again.",
                     retry_after=error.seconds, args=args)
        return 1
    except (ValueError, RPCError, OSError, EOFError, sqlite3.Error) as error:
        details = {}
        if isinstance(error, CommandError):
            code = error.error_code
        elif isinstance(error, RPCError):
            code = "rpc_error"
            details = {"rpc_type": type(error).__name__, "rpc_code": error.code}
        elif isinstance(error, FileExistsError):
            code = "file_exists"
        elif isinstance(error, (ConnectionError, TimeoutError)):
            code = "network_error"
        elif isinstance(error, OSError):
            code = "io_error"
        elif isinstance(error, sqlite3.Error):
            code = "database_error"
            if isinstance(error, sqlite3.OperationalError) and any(word in str(error).lower() for word in ("locked", "busy")):
                error = (f"{error}. Another tcli/Telethon process may be using the session. "
                         "Stop other CLI processes (especially login/logout), then retry. "
                         "Do not delete account.session or its SQLite sidecars. "
                         "Use tcli doctor to locate the session; doctor --connect also needs database access.")
        else:
            code = "invalid_input"
        report_error(getattr(args, "json", False), code, error, args=args, **details)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
