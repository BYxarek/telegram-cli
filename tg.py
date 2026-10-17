"""Telegram chats and messages from your terminal, using your personal account."""

import argparse
import asyncio
from getpass import getpass
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError


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
    commands = cli.add_subparsers(dest="command", title="commands", metavar="COMMAND")
    commands.add_parser("config", help="Save API credentials locally",
                        description="Save or replace API_ID and API_HASH. No Telegram connection required.")
    commands.add_parser("login", help="Sign in to your personal account",
                        description="Sign in with your phone number, login code, and optional 2FA password.")
    commands.add_parser("logout", help="Revoke this CLI session",
                        description="Revoke this CLI session. API credentials are kept for your next login.")
    chats = commands.add_parser("chats", help="List chats and their IDs",
                                description="List chat IDs, names, and unread counts.")
    chats.add_argument("--limit", type=positive_int, default=50, metavar="N",
                       help="maximum number of chats (default: 50)")
    for name, help_text, example in (
            ("read", "Read recent messages, oldest first", 'tcli read "Chat name" --limit 10'),
            ("send", "Send a plain-text message", 'tcli send me "Hello from tcli!"'),
            ("watch", "Follow new messages until Ctrl+C", 'tcli watch -- -1001234567890')):
        command = commands.add_parser(name, help=help_text, description=help_text,
                                       epilog=f"Example: {example}")
        command.add_argument("chat", metavar="CHAT",
                             help="me, @username, exact chat name, or ID from tcli chats; use -- before negative IDs")
        if name == "read":
            command.add_argument("--limit", type=positive_int, default=20, metavar="N",
                                 help="number of recent messages (default: 20)")
        elif name == "send":
            command.add_argument("text", metavar="TEXT", help="message text in quotes; Markdown is not interpreted")
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
    temporary = None
    try:
        # Replace only a complete file, so interrupted writes preserve existing settings.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".config-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"api_id": api_id, "api_hash": api_hash}, stream, indent=2)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Configuration saved: {path}")
    if "TG_API_ID" in os.environ or "TG_API_HASH" in os.environ:
        print("Note: TG_API_ID and TG_API_HASH environment variables override the configuration file.")


def safe_text(value):
    # Remote messages must not execute terminal escape sequences or hide text with bidi controls.
    return "".join(c for c in str(value) if c in "\n\t" or
                   not unicodedata.category(c).startswith("C"))


def show_message(message):
    date = message.date.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    sender = "you" if message.out else str(message.sender_id or "?")
    body = message.raw_text or "[media or service message]"
    print(f"[{date}] {sender} (#{message.id}): {safe_text(body)}", flush=True)


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
    await client.connect()
    try:
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
        if args.command == "logout":
            await client.log_out()
            print("CLI session revoked.")
        elif args.command == "chats":
            async for dialog in client.iter_dialogs(limit=args.limit):
                name = safe_text(dialog.name).replace("\n", " ").replace("\t", " ")
                print(f"{dialog.id}\t{name}\tunread: {dialog.unread_count}")
        else:
            chat = await resolve_chat(client, args.chat)
            if args.command == "read":
                messages = await client.get_messages(chat, limit=args.limit)
                for message in reversed(messages):
                    show_message(message)
            elif args.command == "send":
                if not args.text.strip():
                    raise ValueError("Message text must not be empty.")
                message = await client.send_message(chat, args.text, parse_mode=None)
                print(f"Message sent. ID: {message.id}")
            elif args.command == "watch":
                async def on_message(event):
                    show_message(event.message)
                client.add_event_handler(on_message, events.NewMessage(chats=chat))
                print("Watching for new messages. Press Ctrl+C to stop.", flush=True)
                await client.run_until_disconnected()
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
        session_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "telegram-cli"
        config_path = session_dir / "config.json"
        if args.command == "config":
            configure(config_path)
            return 0
        api_id, api_hash = load_credentials(config_path)
        session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        client = TelegramClient(str(session_dir / "account"), api_id, api_hash,
                                flood_sleep_threshold=0)
        asyncio.run(run(client, args))
    except KeyboardInterrupt:
        return 130
    except FloodWaitError as error:
        print(f"Telegram rate limit: wait {error.seconds} seconds before trying again.", file=sys.stderr)
        return 1
    except (ValueError, RPCError, OSError, EOFError) as error:
        print(f"Error: {safe_text(error)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
