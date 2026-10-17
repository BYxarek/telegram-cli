"""Минимальный Telegram CLI для личного аккаунта."""

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
        raise argparse.ArgumentTypeError("Укажите число больше нуля.")
    return number


def parser():
    cli = argparse.ArgumentParser(prog="tcli", description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("config", help="Сохранить API_ID и API_HASH локально (без входа в Telegram)")
    commands.add_parser("login", help="Войти в личный аккаунт")
    commands.add_parser("logout", help="Завершить текущую CLI-сессию")
    chats = commands.add_parser("chats", help="Показать чаты и их ID")
    chats.add_argument("--limit", type=positive_int, default=50)
    for name, help_text in (("read", "Прочитать историю"),
                            ("send", "Отправить текст"),
                            ("watch", "Показывать новые сообщения до Ctrl+C")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("chat", help="ID из chats, @username, me или точное название")
        if name == "read":
            command.add_argument("--limit", type=positive_int, default=20)
        elif name == "send":
            command.add_argument("text", help="Текст сообщения в кавычках")
    return cli


def validate_credentials(api_id, api_hash):
    if (isinstance(api_id, bool) or not isinstance(api_id, (int, str)) or
            not re.fullmatch(r"[0-9]+", str(api_id)) or int(api_id) <= 0 or
            not isinstance(api_hash, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash)):
        raise ValueError("Нужны положительный API_ID и API_HASH из 32 hex-символов. Выполните: tcli config")
    return int(api_id), api_hash


def load_credentials(path):
    if "TG_API_ID" in os.environ or "TG_API_HASH" in os.environ:
        return validate_credentials(os.environ.get("TG_API_ID"), os.environ.get("TG_API_HASH"))
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("Настройки отсутствуют. Выполните: tcli config") from None
    except (ValueError, UnicodeError):
        raise ValueError("Файл настроек повреждён. Выполните: tcli config") from None
    if not isinstance(config, dict):
        raise ValueError("В файле настроек нужен JSON-объект. Выполните: tcli config")
    return validate_credentials(config.get("api_id"), config.get("api_hash"))


def configure(path):
    api_id = input("API_ID из my.telegram.org: ").strip()
    api_hash = getpass("API_HASH (ввод скрыт): ").strip()
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
    print(f"Настройки сохранены: {path}")
    if "TG_API_ID" in os.environ or "TG_API_HASH" in os.environ:
        print("Учтите: TG_API_ID и TG_API_HASH из окружения имеют приоритет над файлом.")


def safe_text(value):
    # Remote messages must not execute terminal escape sequences or hide text with bidi controls.
    return "".join(c for c in str(value) if c in "\n\t" or
                   not unicodedata.category(c).startswith("C"))


def show_message(message):
    date = message.date.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    sender = "вы" if message.out else str(message.sender_id or "?")
    body = message.raw_text or "[медиа или служебное сообщение]"
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
        raise ValueError("Чат не найден. Используйте ID из команды chats или @username.")
    if len(matches) > 1:
        raise ValueError("Несколько чатов с таким названием. Укажите ID из команды chats.")
    return matches[0]


async def run(client, args):
    await client.connect()
    try:
        if args.command == "login":
            phone = input("Номер телефона с кодом страны: ").strip()
            if not re.fullmatch(r"\+\d{7,15}", phone):
                raise ValueError("Нужен номер вида +79991234567.")
            await client.start(phone=phone,
                               code_callback=lambda: getpass("Код входа из Telegram: "),
                               password=lambda: getpass("Пароль двухфакторной защиты: "))
            me = await client.get_me()
            if me.bot:
                await client.log_out()
                raise ValueError("Нужен личный аккаунт, а не бот.")
            print(f"Вход выполнен: {safe_text(me.first_name)} (ID {me.id}).")
            return
        if not await client.is_user_authorized():
            raise ValueError("Сначала выполните: tcli login")
        if args.command == "logout":
            await client.log_out()
            print("CLI-сессия завершена.")
        elif args.command == "chats":
            async for dialog in client.iter_dialogs(limit=args.limit):
                name = safe_text(dialog.name).replace("\n", " ").replace("\t", " ")
                print(f"{dialog.id}\t{name}\tнепрочитано: {dialog.unread_count}")
        else:
            chat = await resolve_chat(client, args.chat)
            if args.command == "read":
                messages = await client.get_messages(chat, limit=args.limit)
                for message in reversed(messages):
                    show_message(message)
            elif args.command == "send":
                if not args.text.strip():
                    raise ValueError("Сообщение не должно быть пустым.")
                message = await client.send_message(chat, args.text, parse_mode=None)
                print(f"Отправлено. ID сообщения: {message.id}")
            elif args.command == "watch":
                async def on_message(event):
                    show_message(event.message)
                client.add_event_handler(on_message, events.NewMessage(chats=chat))
                print("Ожидание новых сообщений. Выход: Ctrl+C.", flush=True)
                await client.run_until_disconnected()
    finally:
        await client.disconnect()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = parser().parse_args()
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
        print(f"Telegram просит подождать {error.seconds} секунд. Отправка не повторялась.", file=sys.stderr)
        return 1
    except (ValueError, RPCError, OSError, EOFError) as error:
        print(f"Ошибка: {safe_text(error)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
