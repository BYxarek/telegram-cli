import unittest
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import tg


class HelpTests(unittest.TestCase):
    def command(self, *args):
        with TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment.pop("TG_API_ID", None)
            environment.pop("TG_API_HASH", None)
            environment["LOCALAPPDATA"] = directory
            result = subprocess.run(
                [sys.executable, str(Path(tg.__file__).resolve()), *args],
                cwd=directory, env=environment, capture_output=True, text=True,
                encoding="utf-8", timeout=10)
            self.assertEqual(list(Path(directory).iterdir()), [])
        return result

    def test_no_arguments_and_general_help_succeed_without_configuration(self):
        outputs = []
        for args in ((), ("help",), ("--help",), ("-h",)):
            with self.subTest(args=args):
                result = self.command(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("tcli config", result.stdout)
                self.assertIn("tcli login", result.stdout)
                self.assertIn("tcli help send", result.stdout)
                self.assertIn("-- -1001234567890", result.stdout)
                outputs.append(result.stdout)
        self.assertTrue(all(output == outputs[0] for output in outputs))

    def test_command_help_matches_help_flag(self):
        for topic in ("config", "login", "logout", "chats", "read", "send", "watch", "help"):
            with self.subTest(topic=topic):
                result = self.command("help", topic)
                flag = self.command(topic, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, flag.stdout)
                self.assertIn(f"usage: tcli {topic}", result.stdout)

    def test_invalid_commands_remain_errors(self):
        for args in (("help", "unknown"), ("unknown",), ("send",),
                     ("read", "me", "--limit", "0")):
            with self.subTest(args=args):
                result = self.command(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("error:", result.stderr)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "telegram-cli" / "config.json"
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def configure(self, api_id="12345", api_hash="a" * 32):
        with patch("builtins.input", return_value=api_id), \
                patch("tg.getpass", return_value=api_hash), patch("builtins.print") as output:
            tg.configure(self.path)
        return output

    def test_config_saves_and_loads_without_exposing_hash(self):
        output = self.configure()
        self.assertEqual(tg.load_credentials(self.path), (12345, "a" * 32))
        self.assertNotIn("a" * 32, str(output.call_args_list))
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_config_replaces_existing_values(self):
        self.configure()
        self.configure("67890", "b" * 32)
        self.assertEqual(tg.load_credentials(self.path), (67890, "b" * 32))

    def test_invalid_input_preserves_existing_file(self):
        self.configure()
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.configure("0", "invalid")
        self.assertEqual(self.path.read_bytes(), original)

    def test_failed_replace_preserves_file_and_cleans_temporary(self):
        self.configure()
        original = self.path.read_bytes()
        with patch("tg.Path.replace", side_effect=OSError("disk error")), self.assertRaises(OSError):
            self.configure("67890", "b" * 32)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_environment_pair_overrides_file(self):
        self.configure()
        with patch.dict(os.environ, {"TG_API_ID": "67890", "TG_API_HASH": "b" * 32}):
            self.assertEqual(tg.load_credentials(self.path), (67890, "b" * 32))
        with patch.dict(os.environ, {"TG_API_ID": "67890"}), self.assertRaises(ValueError):
            tg.load_credentials(self.path)

    def test_missing_file_suggests_config(self):
        with self.assertRaisesRegex(ValueError, "tcli config"):
            tg.load_credentials(self.path)

    def test_malformed_files_and_types_are_rejected_without_secret_output(self):
        self.path.parent.mkdir()
        for data in (b"{", b"\xff", b"[]", b"{}",
                     json.dumps({"api_id": True, "api_hash": "a" * 32}).encode(),
                     json.dumps({"api_id": 123, "api_hash": 456}).encode()):
            with self.subTest(data=data):
                self.path.write_bytes(data)
                with self.assertRaisesRegex(ValueError, "tcli config") as error:
                    tg.load_credentials(self.path)
                self.assertNotIn("a" * 32, str(error.exception))

    def test_config_command_does_not_create_telegram_client(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.path.parent.parent)}), \
                patch("sys.argv", ["tg.py", "config"]), \
                patch("tg.sys.stdout.reconfigure"), patch("tg.sys.stderr.reconfigure"), \
                patch("builtins.input", return_value="12345"), \
                patch("tg.getpass", return_value="a" * 32), \
                patch("builtins.print"), patch("tg.TelegramClient") as client:
            self.assertEqual(tg.main(), 0)
        client.assert_not_called()
        self.assertEqual(tg.load_credentials(self.path), (12345, "a" * 32))


class FakeClient:
    def __init__(self, dialogs=()):
        self.dialogs = dialogs
        self.connect = AsyncMock()
        self.disconnect = AsyncMock()
        self.is_user_authorized = AsyncMock(return_value=True)
        self.get_input_entity = AsyncMock(return_value="self")
        self.send_message = AsyncMock(return_value=SimpleNamespace(id=42))
        self.get_messages = AsyncMock(return_value=[])

    async def iter_dialogs(self, **kwargs):
        for dialog in self.dialogs:
            yield dialog


class CliTests(unittest.IsolatedAsyncioTestCase):
    def test_arguments(self):
        args = tg.parser().parse_args(["send", "--", "-100123", "Привет"])
        self.assertEqual((args.chat, args.text), ("-100123", "Привет"))
        with self.assertRaises(SystemExit), patch("sys.stderr"):
            tg.parser().parse_args(["read", "me", "--limit", "0"])

    def test_terminal_controls_removed(self):
        self.assertEqual(tg.safe_text("a\x1b[2J\u202eb\n"), "a[2Jb\n")

    async def test_resolve_id_and_reject_ambiguous_title(self):
        client = FakeClient([
            SimpleNamespace(id=-1001, name="Чат", input_entity="first"),
            SimpleNamespace(id=-1002, name="Чат", input_entity="second"),
        ])
        self.assertEqual(await tg.resolve_chat(client, "-1002"), "second")
        with self.assertRaisesRegex(ValueError, "Multiple chats"):
            await tg.resolve_chat(client, "Чат")
        with self.assertRaisesRegex(ValueError, "Chat not found"):
            await tg.resolve_chat(client, "Нет")

    async def test_send_literal_text_once(self):
        client = FakeClient()
        args = tg.parser().parse_args(["send", "me", "**Привет**"])
        with patch("builtins.print"):
            await tg.run(client, args)
        client.send_message.assert_awaited_once_with("self", "**Привет**", parse_mode=None)
        client.disconnect.assert_awaited_once()

    async def test_unauthorized_does_not_send(self):
        client = FakeClient()
        client.is_user_authorized.return_value = False
        with self.assertRaisesRegex(ValueError, "login"):
            await tg.run(client, tg.parser().parse_args(["send", "me", "Привет"]))
        client.send_message.assert_not_awaited()
        client.disconnect.assert_awaited_once()

    async def test_read_prints_chronologically(self):
        client = FakeClient()
        client.get_messages.return_value = ["new", "old"]
        with patch("tg.show_message") as show:
            await tg.run(client, tg.parser().parse_args(["read", "me"]))
        self.assertEqual([call.args[0] for call in show.call_args_list], ["old", "new"])

    async def test_disconnect_on_send_error(self):
        client = FakeClient()
        client.send_message.side_effect = OSError("offline")
        with self.assertRaises(OSError):
            await tg.run(client, tg.parser().parse_args(["send", "me", "Привет"]))
        client.disconnect.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
