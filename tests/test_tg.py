import unittest
import json
import os
import subprocess
import sys
import io
import sqlite3
from datetime import datetime, timezone
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import tg
from importlib.resources import files


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
        for topic in ("config", "login", "logout", "doctor", "chats", "read", "send", "watch", "skills", "help"):
            with self.subTest(topic=topic):
                result = self.command("help", topic)
                flag = self.command(topic, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, flag.stdout)
                self.assertIn(f"usage: tcli {topic}", result.stdout)

    def test_invalid_commands_remain_errors(self):
        for args in (("help", "unknown"), ("unknown",), ("send",),
                     ("send", "me"), ("send", "me", "text", "--stdin"),
                     ("send", "me", "--stdin", "--file", "text.txt"),
                     ("send", "me", "text", "--reply-to", "0"),
                     ("send", "me", "text", "--format", "unknown"),
                     ("read", "me", "--before", "0"),
                     ("read", "me", "--limit", "0")):
            with self.subTest(args=args):
                result = self.command(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("error:", result.stderr)

    def test_version_and_nested_help_without_configuration(self):
        result = self.command("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), tg.__version__)
        result = self.command("skills", "install", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--agent", result.stdout)
        for args in (("skills",), ("skills", "unknown"), ("skills", "install", "--agent", "unknown")):
            self.assertEqual(self.command(*args).returncode, 2)


class SkillsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(TemporaryDirectory()))
        self.stack.enter_context(patch("tg.Path.home", return_value=self.home))
        self.stack.enter_context(patch("builtins.print"))
        self.content = files("tcli_skills").joinpath("SKILL.md").read_bytes()

    def path(self, agent):
        return self.home / (".agents" if agent == "codex" else ".claude") / "skills" / "telegram-cli" / "SKILL.md"

    def test_install_both_is_offline_and_idempotent(self):
        with patch.dict(os.environ, {}, clear=True), patch("sys.argv", ["tcli", "skills", "install"]), \
                patch("tg.sys.stdout.reconfigure"), patch("tg.sys.stderr.reconfigure"), \
                patch("tg.TelegramClient") as client, patch("tg.agent_installed", return_value=True):
            self.assertEqual(tg.main(), 0)
            client.assert_not_called()
        tg.install_skills("all")
        for agent in ("codex", "claude"):
            path = self.path(agent)
            self.assertEqual(path.read_bytes(), self.content)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_auto_creates_nothing_without_agents(self):
        self.assertEqual(tg.parser().parse_args(["skills", "install"]).agent, "auto")
        with patch("tg.agent_installed", return_value=False):
            tg.install_skills("auto")
        self.assertEqual(list(self.home.iterdir()), [])

    def test_auto_installs_only_detected_agent(self):
        for detected in ("codex", "claude"):
            with self.subTest(agent=detected), TemporaryDirectory() as directory:
                home = Path(directory)
                with patch("tg.Path.home", return_value=home), \
                        patch("tg.agent_installed", side_effect=lambda name: name == detected):
                    tg.install_skills("auto")
                self.assertEqual((home / (".agents" if detected == "codex" else ".claude") /
                                  "skills" / "telegram-cli" / "SKILL.md").read_bytes(), self.content)
                self.assertFalse((home / (".claude" if detected == "codex" else ".agents")).exists())

    def test_explicit_agent_does_not_require_detection(self):
        with patch("tg.agent_installed", return_value=False) as detect:
            tg.install_skills("all")
            detect.assert_not_called()
        self.assertTrue(self.path("codex").exists())
        self.assertTrue(self.path("claude").exists())

    def test_detect_path_command(self):
        with patch("tg.shutil.which", return_value="executable"):
            self.assertTrue(tg.agent_installed("claude"))

    def test_detect_native_install_outside_path(self):
        suffix = ".exe" if sys.platform == "win32" else ""
        executable = self.home / ".local" / "bin" / ("claude" + suffix)
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"test")
        executable.chmod(0o700)
        with patch("tg.shutil.which", return_value=None):
            self.assertTrue(tg.agent_installed("claude"))

    def test_config_directories_are_not_installations(self):
        for name in (".codex", ".claude", ".agents"):
            (self.home / name).mkdir()
        with patch("tg.shutil.which", return_value=None), patch("tg.Path.is_file", return_value=False), \
                patch("tg.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="")):
            self.assertFalse(tg.agent_installed("codex"))
            self.assertFalse(tg.agent_installed("claude"))

    def test_detect_windows_store_codex_and_query_failure(self):
        with patch("tg.sys.platform", "win32"), patch("tg.shutil.which", return_value=None), \
                patch("tg.Path.is_file", return_value=False), patch("tg.subprocess.run") as query:
            query.return_value = SimpleNamespace(returncode=0, stdout="OpenAI.Codex\n")
            self.assertTrue(tg.agent_installed("codex"))
            self.assertEqual(query.call_args.kwargs["timeout"], 5)
            query.return_value = SimpleNamespace(returncode=0, stdout="")
            self.assertFalse(tg.agent_installed("codex"))
            query.side_effect = subprocess.TimeoutExpired("powershell", 5)
            self.assertFalse(tg.agent_installed("codex"))

    def test_detect_mac_codex_app(self):
        executable = self.home / "Applications" / "Codex.app" / "Contents" / "MacOS" / "Codex"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"test")
        with patch("tg.sys.platform", "darwin"), patch("tg.shutil.which", return_value=None):
            self.assertTrue(tg.agent_installed("codex"))

    def test_update_backs_up_custom_text_and_preserves_other_files(self):
        path = self.path("codex")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"custom instructions")
        other = path.parent / "notes.txt"
        other.write_text("notes")
        tg.install_skills("codex")
        self.assertEqual(path.read_bytes(), self.content)
        backups = list(path.parent.glob("SKILL.md.backup-*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"custom instructions")
        self.assertEqual(other.read_text(), "notes")
        self.assertFalse(self.path("claude").exists())

    def test_claude_only(self):
        tg.install_skills("claude")
        self.assertEqual(self.path("claude").read_bytes(), self.content)
        self.assertFalse(self.path("codex").exists())

    def test_replace_failure_preserves_existing_skill(self):
        path = self.path("codex")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"original")
        with patch("tg.Path.replace", side_effect=OSError("disk error")), self.assertRaises(OSError):
            tg.install_skills("codex")
        self.assertEqual(path.read_bytes(), b"original")
        self.assertEqual(len(list(path.parent.glob("*.bak"))), 1)
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_backup_failure_does_not_overwrite_skill(self):
        path = self.path("codex")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"original")
        with patch("tg.tempfile.NamedTemporaryFile", side_effect=OSError("disk full")), self.assertRaises(OSError):
            tg.install_skills("codex")
        self.assertEqual(path.read_bytes(), b"original")

    def test_symlink_is_rejected(self):
        path = self.path("codex")
        with patch("tg.Path.is_symlink", return_value=True), self.assertRaisesRegex(ValueError, "symlinked"):
            tg.install_skills("codex")
        self.assertFalse(path.exists())

    def test_bundled_and_repository_skills_match(self):
        root = Path(__file__).resolve().parents[1]
        for folder in (".agents", ".claude"):
            self.assertEqual((root / folder / "skills" / "telegram-cli" / "SKILL.md").read_bytes(), self.content)


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
        self.start = AsyncMock()
        self.get_me = AsyncMock(return_value=SimpleNamespace(bot=False, first_name="User", id=1))
        self.log_out = AsyncMock()
        self.is_connected = Mock(return_value=False)
        self.session = SimpleNamespace(save=Mock())

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

    async def test_send_explicit_formats_with_reply(self):
        for mode in ("plain", "markdown", "html"):
            client = FakeClient()
            with patch("builtins.print"):
                await tg.run(client, tg.parser().parse_args(
                    ["send", "me", "**text**", "--format", mode, "--reply-to", "1"]))
            client.send_message.assert_awaited_once_with(
                "self", "**text**", parse_mode=None if mode == "plain" else mode, reply_to=1)

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

    async def test_disconnect_on_connect_error(self):
        client = FakeClient()
        client.connect.side_effect = OSError("offline")
        with self.assertRaises(OSError):
            await tg.run(client, tg.parser().parse_args(["read", "me"]))
        client.disconnect.assert_awaited_once()

    async def test_reply_once_and_reject_blank_text(self):
        client = FakeClient()
        with patch("builtins.print"):
            await tg.run(client, tg.parser().parse_args(["send", "me", "reply", "--reply-to", "123"]))
        client.send_message.assert_awaited_once_with("self", "reply", parse_mode=None, reply_to=123)
        client.send_message.reset_mock()
        with self.assertRaisesRegex(ValueError, "empty"):
            await tg.run(client, tg.parser().parse_args(["send", "me", " \n"]))
        client.send_message.assert_not_awaited()

    def message(self, message_id=1, text="Привет\n\x1b\u202e"):
        return SimpleNamespace(id=message_id, chat_id=-1001, date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                               sender_id=2, out=False, raw_text=text, media=None)

    async def test_json_history_search_and_pagination(self):
        client = FakeClient()
        client.get_messages.return_value = [self.message(2), self.message(1, None)]
        output = io.StringIO()
        with redirect_stdout(output):
            await tg.run(client, tg.parser().parse_args(
                ["read", "me", "--json", "--search", "Привет", "--before", "9", "--limit", "2"]))
        client.get_messages.assert_awaited_once_with("self", limit=2, search="Привет", offset_id=9)
        rows = json.loads(output.getvalue())
        self.assertEqual([row["id"] for row in rows], [1, 2])
        self.assertEqual(rows[0]["text"], "")
        self.assertEqual(rows[1], {"id": 2, "chat_id": -1001, "date": "2026-01-01T00:00:00+00:00",
                                  "sender_id": 2, "out": False, "text": "Привет\n\x1b\u202e", "has_media": False})
        self.assertNotIn("\x1b", output.getvalue())
        self.assertNotIn("\u202e", output.getvalue())

    async def test_json_chats_and_empty_arrays(self):
        for dialogs in ([], [SimpleNamespace(id=1, name="Чат\n\x1b", unread_count=3)]):
            client = FakeClient(dialogs)
            output = io.StringIO()
            with redirect_stdout(output):
                await tg.run(client, tg.parser().parse_args(["chats", "--json"]))
            self.assertEqual(json.loads(output.getvalue()),
                             [{"id": d.id, "name": d.name, "unread_count": d.unread_count} for d in dialogs])
        output = io.StringIO()
        with redirect_stdout(output):
            await tg.run(FakeClient(), tg.parser().parse_args(["read", "me", "--json"]))
        self.assertEqual(json.loads(output.getvalue()), [])

    async def test_watch_output_and_disconnect(self):
        for json_mode in (False, True):
            client = FakeClient()
            client.is_connected.side_effect = [True, False]
            client.get_messages.side_effect = [[self.message(10)], [self.message(11), self.message(12)]]
            output, errors = io.StringIO(), io.StringIO()
            args = ["watch", "me"] + (["--json"] if json_mode else [])
            with redirect_stdout(output), redirect_stderr(errors), patch("tg.asyncio.sleep", new_callable=AsyncMock):
                await tg.run(client, tg.parser().parse_args(args))
            if json_mode:
                self.assertEqual([json.loads(line)["id"] for line in output.getvalue().splitlines()], [11, 12])
                self.assertIn("Watching", errors.getvalue())
            else:
                self.assertIn("Watching", output.getvalue())
                self.assertIn("(#11):", output.getvalue())
                self.assertNotIn("\x1b", output.getvalue())
            self.assertNotIn("(#10):", output.getvalue())
            self.assertEqual(client.get_messages.await_args_list,
                             [call("self", limit=1), call("self", min_id=10, reverse=True, limit=None)])
            self.assertEqual(client.session.save.call_count, 2)
            client.disconnect.assert_awaited_once()

    async def test_watch_empty_chat_and_cursor_progress(self):
        client = FakeClient()
        client.is_connected.side_effect = [True, True, True, False]
        client.get_messages.side_effect = [[], [self.message(1)], [], [self.message(2)]]
        async def pause(seconds):
            self.assertEqual(seconds, 2)
            self.assertEqual(client.session.save.call_count, client.get_messages.await_count)
        with patch("tg.asyncio.sleep", side_effect=pause), patch("tg.show_message") as show, patch("builtins.print"):
            await tg.run(client, tg.parser().parse_args(["watch", "me"]))
        self.assertEqual([entry.args[0].id for entry in show.call_args_list], [1, 2])
        self.assertEqual([entry.kwargs.get("min_id") for entry in client.get_messages.await_args_list], [None, 0, 1, 1])

    async def test_watch_releases_real_sqlite_write_lock_before_wait(self):
        from telethon.sessions import SQLiteSession
        with TemporaryDirectory() as directory:
            path = str(Path(directory) / "account")
            client = FakeClient()
            client.session = SQLiteSession(path)
            client.is_connected.side_effect = [True, False]
            async def fetch(*args, **kwargs):
                client.session.set_dc(2, "149.154.167.51", 443)
                return []
            client.get_messages.side_effect = fetch
            async def pause(seconds):
                connection = sqlite3.connect(path + ".session", timeout=0)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.rollback()
                finally:
                    connection.close()
            try:
                with patch("tg.asyncio.sleep", side_effect=pause), patch("builtins.print"):
                    await tg.run(client, tg.parser().parse_args(["watch", "me"]))
            finally:
                client.session.close()

    async def test_watch_cancellation_disconnects(self):
        import asyncio
        client = FakeClient()
        client.is_connected.return_value = True
        with patch("tg.asyncio.sleep", side_effect=asyncio.CancelledError), patch("builtins.print"):
            with self.assertRaises(asyncio.CancelledError):
                await tg.run(client, tg.parser().parse_args(["watch", "me"]))
        client.disconnect.assert_awaited_once()

    async def test_watch_error_disconnects(self):
        client = FakeClient()
        client.is_connected.return_value = True
        client.get_messages.side_effect = [[], OSError("offline")]
        with patch("builtins.print"), self.assertRaises(OSError):
            await tg.run(client, tg.parser().parse_args(["watch", "me"]))
        client.disconnect.assert_awaited_once()

    async def test_login_logout_and_bot_rejection(self):
        for bot in (False, True):
            client = FakeClient()
            client.get_me.return_value.bot = bot
            with patch("builtins.input", return_value="+15551234567"), patch("builtins.print"):
                if bot:
                    with self.assertRaisesRegex(ValueError, "personal"):
                        await tg.run(client, tg.parser().parse_args(["login"]))
                    client.log_out.assert_awaited_once()
                else:
                    await tg.run(client, tg.parser().parse_args(["login"]))
                    client.start.assert_awaited_once()
            client.disconnect.assert_awaited_once()
        client = FakeClient()
        with patch("builtins.print"):
            await tg.run(client, tg.parser().parse_args(["logout"]))
        client.log_out.assert_awaited_once()
        client.disconnect.assert_awaited_once()

    async def test_invalid_phone_does_not_login(self):
        client = FakeClient()
        with patch("builtins.input", return_value="invalid"), self.assertRaisesRegex(ValueError, "international"):
            await tg.run(client, tg.parser().parse_args(["login"]))
        client.start.assert_not_awaited()
        client.disconnect.assert_awaited_once()


class MainTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {"LOCALAPPDATA": str(self.directory),
                                                       "TG_API_ID": "123", "TG_API_HASH": "a" * 32}, clear=True))
        self.output, self.errors = io.StringIO(), io.StringIO()
        self.stack.enter_context(patch("tg.sys.stdout", self.output))
        self.stack.enter_context(patch("tg.sys.stderr", self.errors))
        self.output.reconfigure = lambda **kwargs: None
        self.errors.reconfigure = lambda **kwargs: None
        self.client = FakeClient()
        self.factory = self.stack.enter_context(patch("tg.TelegramClient", return_value=self.client))

    def main(self, *args):
        with patch("sys.argv", ["tcli", *args]):
            return tg.main()

    def test_send_file_and_stdin_preserve_multiline_text(self):
        text = "Привет\n**literal**\n"
        path = self.directory / "message.txt"
        path.write_text(text, encoding="utf-8-sig")
        for args in (("send", "me", "--file", str(path)), ("send", "me", "--stdin")):
            with io.TextIOWrapper(io.BytesIO(text.encode("utf-8")), encoding="utf-8") as stream, patch("sys.stdin", stream):
                self.assertEqual(self.main(*args), 0)
            self.client.send_message.assert_awaited_once_with("self", text, parse_mode=None)
            self.client.send_message.reset_mock()

    def test_file_and_stdin_support_formatting(self):
        text = "<b>Bold</b> <u>underlined</u>"
        path = self.directory / "message.html"
        path.write_text(text, encoding="utf-8")
        for args in (("send", "me", "--file", str(path), "--format", "html"),
                     ("send", "me", "--stdin", "--format", "html")):
            with io.TextIOWrapper(io.BytesIO(text.encode()), encoding="utf-8") as stream, patch("sys.stdin", stream):
                self.assertEqual(self.main(*args), 0)
            self.client.send_message.assert_awaited_once_with("self", text, parse_mode="html")
            self.client.send_message.reset_mock()

    def test_invalid_input_does_not_connect(self):
        path = self.directory / "message.txt"
        for content in (b" \n", b"\xff"):
            path.write_bytes(content)
            self.assertEqual(self.main("send", "me", "--file", str(path)), 1)
        path.unlink()
        self.assertEqual(self.main("send", "me", "--file", str(path)), 1)
        self.factory.assert_not_called()

    def test_doctor_local_and_connected(self):
        self.assertEqual(self.main("doctor"), 1)
        self.assertFalse((self.directory / "telegram-cli").exists())
        self.factory.assert_not_called()
        session = self.directory / "telegram-cli" / "account.session"
        session.parent.mkdir()
        session.touch()
        self.assertEqual(self.main("doctor"), 0)
        self.factory.assert_not_called()
        self.assertEqual(self.main("doctor", "--connect"), 0)
        self.client.is_user_authorized.assert_awaited_once()
        self.client.disconnect.assert_awaited_once()
        self.assertNotIn("a" * 32, self.output.getvalue() + self.errors.getvalue())

    def test_doctor_invalid_configuration_and_unauthorized_session(self):
        with patch.dict(os.environ, {"TG_API_HASH": "invalid"}):
            self.assertEqual(self.main("doctor"), 1)
        self.factory.assert_not_called()
        session = self.directory / "telegram-cli" / "account.session"
        session.parent.mkdir()
        session.touch()
        self.client.is_user_authorized.return_value = False
        self.assertEqual(self.main("doctor", "--connect"), 1)
        self.assertIn("tcli login", self.errors.getvalue())

    def test_handled_errors_and_exit_codes(self):
        for error, code, expected in (
                (tg.FloodWaitError(request=None, capture=17), 1, "wait 17 seconds"),
                (tg.RPCError(request=None, message="denied", code=400), 1, "denied"),
                (OSError("offline"), 1, "offline"),
                (sqlite3.OperationalError("database is locked"), 1, "database is locked"),
                (KeyboardInterrupt(), 130, "")):
            with self.subTest(error=type(error).__name__):
                self.client.connect.side_effect = error
                self.client.disconnect.reset_mock()
                self.assertEqual(self.main("read", "me"), code)
                self.assertIn(expected, self.errors.getvalue())
                self.client.disconnect.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
