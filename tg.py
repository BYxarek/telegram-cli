"""Telegram chats and messages from your terminal, using your personal account."""

import argparse
import asyncio
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
import unicodedata

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from tcli_skills import __version__


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Enter an integer greater than zero.")
    return number


def parser():
    cli = argparse.ArgumentParser(
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
    skills = commands.add_parser("skills", help="Install or update bundled agent skills without Telegram access")
    skill_commands = skills.add_subparsers(dest="skills_command", required=True)
    install = skill_commands.add_parser("install", help="Install or update user skills, backing up changed files")
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
    for name, help_text, example in (
            ("read", "Read recent messages, oldest first", 'tcli read "Chat name" --limit 10'),
            ("send", "Send a text message with optional formatting", 'tcli send me "Hello from tcli!"'),
            ("watch", "Follow new messages until Ctrl+C", 'tcli watch -- -1001234567890')):
        command = commands.add_parser(name, help=help_text, description=help_text,
                                       epilog=f"Example: {example}")
        command.add_argument("chat", metavar="CHAT",
                             help="me, @username, exact chat name, or ID from tcli chats; use -- before negative IDs")
        if name == "read":
            command.add_argument("--limit", type=positive_int, default=20, metavar="N",
                                 help="number of recent messages (default: 20)")
            command.add_argument("--before", type=positive_int, metavar="ID",
                                 help="read messages older than this message ID (exclusive)")
            command.add_argument("--search", metavar="QUERY", help="search message text using Telegram search")
            command.add_argument("--json", action="store_true", help="output a JSON array")
        elif name == "send":
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument("text", nargs="?", metavar="TEXT", help="literal message text in quotes")
            source.add_argument("--stdin", action="store_true", help="read UTF-8 text from stdin until EOF")
            source.add_argument("--file", type=Path, metavar="PATH", help="read text from a UTF-8 file")
            command.add_argument("--reply-to", type=positive_int, metavar="ID", help="reply to a message in this chat")
            command.add_argument("--format", choices=("plain", "markdown", "html"), default="plain",
                                 help="text formatting (default: plain); uses Telethon Markdown or HTML")
        else:
            command.add_argument("--json", action="store_true", help="output one JSON object per line (JSONL)")
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
    body = message.raw_text or "[media or service message]"
    print(f"[{date}] {sender} (#{message.id}): {safe_text(body)}", flush=True)


def message_data(message):
    return {"id": message.id, "chat_id": message.chat_id,
            "date": message.date.isoformat(), "sender_id": message.sender_id,
            "out": message.out, "text": message.raw_text or "",
            "has_media": bool(message.media)}


async def resolve_chat(client, value):
    if value == "me" or value.startswith("@"):
        return await client.get_input_entity(value)
    numeric = re.fullmatch(r"-?\d+", value)
    matches = []
    async for dialog in client.iter_dialogs():
        if (numeric and dialog.id == int(value)) or (not numeric and dialog.name == value):
            matches.append(dialog.input_entity)
    if not matches:
        raise ValueError("Chat not found. Use an ID from tcli chats or an @username.")
    if len(matches) > 1:
        raise ValueError("Multiple chats have this name. Use an ID from tcli chats.")
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
            raise ValueError("You are not signed in. Run: tcli login")
        if args.command == "doctor":
            print("Telegram connection: OK\nSession authorization: OK")
        elif args.command == "logout":
            await client.log_out()
            print("CLI session revoked.")
        elif args.command == "chats":
            dialogs = []
            async for dialog in client.iter_dialogs(limit=args.limit):
                if args.json:
                    dialogs.append({"id": dialog.id, "name": dialog.name, "unread_count": dialog.unread_count})
                else:
                    name = safe_text(dialog.name).replace("\n", " ").replace("\t", " ")
                    print(f"{dialog.id}\t{name}\tunread: {dialog.unread_count}")
            if args.json:
                print(json.dumps(dialogs))
        else:
            chat = await resolve_chat(client, args.chat)
            if args.command == "read":
                options = {}
                if args.before is not None:
                    options["offset_id"] = args.before
                if args.search is not None:
                    options["search"] = args.search
                messages = await client.get_messages(chat, limit=args.limit, **options)
                if args.json:
                    print(json.dumps([message_data(message) for message in reversed(messages)]))
                else:
                    for message in reversed(messages):
                        show_message(message)
            elif args.command == "send":
                if not args.text.strip():
                    raise ValueError("Message text must not be empty.")
                options = {}
                if args.reply_to is not None:
                    options["reply_to"] = args.reply_to
                mode = None if args.format == "plain" else args.format
                message = await client.send_message(chat, args.text, parse_mode=mode, **options)
                print(f"Message sent. ID: {message.id}")
            elif args.command == "watch":
                latest = await client.get_messages(chat, limit=1)
                last_id = latest[0].id if latest else 0
                client.session.save()
                print("Watching for new messages. Press Ctrl+C to stop.",
                      file=sys.stderr if args.json else sys.stdout, flush=True)
                # Shared authorizations do not reliably receive each other's outgoing updates.
                while client.is_connected():
                    messages = await client.get_messages(chat, min_id=last_id, reverse=True, limit=None)
                    # Commit entity-cache writes before sleeping so other CLI processes can use SQLite.
                    client.session.save()
                    for message in messages:
                        if args.json:
                            print(json.dumps(message_data(message)), flush=True)
                        else:
                            show_message(message)
                        last_id = message.id
                    await asyncio.sleep(2)
    finally:
        await client.disconnect()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    cli = parser()
    args = cli.parse_args()
    if args.command is None or args.command == "help":
        if args.command == "help" and args.topic:
            cli.parse_args([args.topic, "--help"])
        cli.print_help()
        return 0
    try:
        if args.command == "skills":
            install_skills(args.agent)
            return 0
        session_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "telegram-cli"
        config_path = session_dir / "config.json"
        if args.command == "config":
            configure(config_path)
            return 0
        if args.command == "send":
            if args.stdin:
                sys.stdin.reconfigure(encoding="utf-8-sig")
                args.text = sys.stdin.read()
            elif args.file is not None:
                args.text = args.file.read_text(encoding="utf-8-sig")
            if not args.text.strip():
                raise ValueError("Message text must not be empty.")
        if args.command == "doctor":
            print(f"Configuration path: {safe_text(config_path)}")
            print(f"Session path: {safe_text(session_dir / 'account.session')}")
        api_id, api_hash = load_credentials(config_path)
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
        return 130
    except FloodWaitError as error:
        print(f"Telegram rate limit: wait {error.seconds} seconds before trying again.", file=sys.stderr)
        return 1
    except (ValueError, RPCError, OSError, EOFError, sqlite3.Error) as error:
        print(f"Error: {safe_text(error)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
