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
        for topic in ("config", "login", "logout", "doctor", "chats", "read", "send", "edit", "download", "forward", "watch", "skills", "help"):
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
                     ("send", "me", "text", "--attach", "photo.jpg"),
                     ("download", "me", "1"),
                     ("download", "me", "0", "--output", "file.bin"),
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
        self.assertIn("~/.agents/skills/telegram-cli/SKILL.md", result.stdout)
        self.assertIn("~/.claude/skills/telegram-cli/SKILL.md", result.stdout)
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


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=True))
        self.stack.enter_context(patch("tg.sys.stdout.isatty", return_value=True))
        self.stack.enter_context(patch("tg.time.time", return_value=100000))
        self.fetch = self.stack.enter_context(patch("tg.urlopen"))
        self.fetch.return_value.__enter__.return_value.read.return_value = b'{"info":{"version":"0.10.0"}}'
        self.output = io.StringIO()
        self.stack.enter_context(redirect_stderr(self.output))
        self.cache = self.root / "telegram-cli" / "cache" / "update-check.json"

    def test_new_version_notice_is_cached_and_uses_numeric_comparison(self):
        tg.check_update()
        tg.check_update()
        self.fetch.assert_called_once()
        self.assertEqual(self.fetch.call_args.kwargs["timeout"], 1)
        self.assertIn("0.10.0", self.output.getvalue())
        self.assertIn("tcli skills install", self.output.getvalue())
        self.assertEqual(json.loads(self.cache.read_text())["latest"], "0.10.0")

    def test_noninteractive_and_opt_out_do_not_use_network_or_create_files(self):
        with patch("tg.sys.stdout.isatty", return_value=False):
            tg.check_update()
        with patch.dict(os.environ, {"TCLI_NO_UPDATE_CHECK": "1"}):
            tg.check_update()
        self.fetch.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_offline_failure_is_silent_and_cached(self):
        self.fetch.side_effect = OSError("offline")
        tg.check_update()
        tg.check_update()
        self.fetch.assert_called_once()
        self.assertEqual(self.output.getvalue(), "")

    def test_stale_cache_is_refreshed_and_invalid_versions_are_not_displayed(self):
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text('{"checked_at":0,"latest":"0.0.1"}')
        self.fetch.return_value.__enter__.return_value.read.return_value = b'{"info":{"version":"bad\\u001b[2J"}}'
        tg.check_update()
        self.fetch.assert_called_once()
        self.assertEqual(self.output.getvalue(), "")

    def test_current_version_bad_response_and_unwritable_cache_are_harmless(self):
        for response in (b"not json", b"[]", json.dumps({"info": {"version": tg.__version__}}).encode()):
            with self.subTest(response=response), patch("tg.atomic_write", side_effect=OSError("read only")):
                self.fetch.return_value.__enter__.return_value.read.return_value = response
                tg.check_update()
                self.assertEqual(self.output.getvalue(), "")

    def test_general_help_checks_but_command_help_and_version_do_not(self):
        with patch("tg.check_update") as check, patch("tg.sys.stdout.reconfigure"), \
                patch("tg.sys.stderr.reconfigure", create=True), patch("builtins.print"):
            for args in ([], ["help"], ["--help"], ["-h"]):
                with patch("sys.argv", ["tcli", *args]):
                    self.assertEqual(tg.main(), 0)
            self.assertEqual(check.call_count, 4)
            check.reset_mock()
            for args in (["read", "--help"], ["help", "read"], ["--version"]):
                with patch("sys.argv", ["tcli", *args]), self.assertRaises(SystemExit):
                    tg.main()
            check.assert_not_called()


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


def fake_message(message_id=42, **changes):
    values = dict(id=message_id, chat_id=1, date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                  sender_id=1, out=True, raw_text="text", media=None, file=None,
                  reply_to_msg_id=None, grouped_id=None)
    values.update(changes)
    return SimpleNamespace(**values)


def fake_file(name):
    return SimpleNamespace(name=name, mime_type="application/octet-stream", size=1,
                           ext=".bin", width=None, height=None, duration=None)


class FakeClient:
    def __init__(self, dialogs=()):
        self.dialogs = dialogs
        self.connect = AsyncMock()
        self.disconnect = AsyncMock()
        self.is_user_authorized = AsyncMock(return_value=True)
        self.get_input_entity = AsyncMock(return_value="self")
        self.send_message = AsyncMock(return_value=fake_message(42))
        self.send_file = AsyncMock(return_value=fake_message(43))
        self.edit_message = AsyncMock(return_value=fake_message(42))
        self.forward_messages = AsyncMock(return_value=fake_message(44))
        self.download_media = AsyncMock()
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
                                  "sender_id": 2, "out": False, "text": "Привет\n\x1b\u202e", "has_media": False,
                              "media": None, "reply_to_id": None, "grouped_id": None, "sender_name": None})
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

    def test_attachment_upload_and_caption(self):
        path = self.directory / "фото.jpg"
        path.write_bytes(b"binary\x00\xff")
        async def upload(chat, stream, **options):
            self.assertEqual(chat, "self")
            self.assertEqual(stream.read(), b"binary\x00\xff")
            self.assertEqual(stream.name, str(path))
            return SimpleNamespace(id=43)
        self.client.send_file.side_effect = upload
        for extra, caption, mode, document in (([], "", None, False),
                (["--caption", "<b>Photo</b>", "--format", "html", "--as-file"],
                 "<b>Photo</b>", "html", True)):
            self.assertEqual(self.main("send", "me", "--attach", str(path), "--reply-to", "12", *extra), 0)
            options = self.client.send_file.await_args.kwargs
            self.assertEqual(options, dict(caption=caption, force_document=document, parse_mode=mode, reply_to=12))
            self.assertTrue(self.client.send_file.await_args.args[1].closed)
        self.client.send_message.assert_not_awaited()
        self.assertIn("ID: 43", self.output.getvalue())

    def test_invalid_attachments_do_not_connect(self):
        for args in (("--attach", str(self.directory / "missing")), ("--attach", str(self.directory)),
                     ("text", "--caption", "caption"), ("text", "--as-file")):
            self.assertEqual(self.main("send", "me", *args), 1)
        self.factory.assert_not_called()

    def test_binary_documents_are_not_decoded_as_text(self):
        content = b"\x00\xff\xfe\x80binary"
        async def upload(chat, stream, **options):
            self.assertEqual(stream.read(), content)
            return SimpleNamespace(id=43)
        self.client.send_file.side_effect = upload
        for extension in ("exe", "apk", "zip", "bin", "mp4", "mp3"):
            with self.subTest(extension=extension):
                path = self.directory / ("attachment." + extension)
                path.write_bytes(content)
                self.assertEqual(self.main("send", "me", "--attach", str(path)), 0)
                self.assertEqual(self.client.send_file.await_args.args[1].name, str(path))
        self.client.send_message.assert_not_awaited()

    def test_attachment_upload_failure_disconnects(self):
        path = self.directory / "file.bin"
        path.write_bytes(b"data")
        self.client.send_file.side_effect = OSError("offline")
        self.assertEqual(self.main("send", "me", "--attach", str(path)), 1)
        self.client.disconnect.assert_awaited_once()
        self.assertTrue(self.client.send_file.await_args.args[1].closed)
        self.assertNotIn("Message sent", self.output.getvalue())

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

    def test_send_json_result(self):
        self.assertEqual(self.main("send", "me", "text", "--json"), 0)
        result = json.loads(self.output.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual(result["messages"], [tg.message_data(self.client.send_message.return_value)])
        self.assertEqual(self.errors.getvalue(), "")

    def test_read_id_and_reply_album_context(self):
        message = fake_message(12, reply_to_msg_id=8, grouped_id=123456)
        self.client.get_messages.return_value = message
        self.assertEqual(self.main("read", "me", "--id", "12", "--json"), 0)
        self.client.get_messages.assert_awaited_once_with("self", ids=12)
        row = json.loads(self.output.getvalue())[0]
        self.assertEqual((row["reply_to_id"], row["grouped_id"]), (8, 123456))
        with patch("builtins.print") as output:
            tg.show_message(message)
        self.assertIn("[reply to #8] [album 123456]", output.call_args.args[0])

    def test_media_filters_combine_with_search_and_pagination(self):
        for name, expected in tg.MEDIA_FILTERS.items():
            with self.subTest(media=name):
                self.assertEqual(self.main("read", "me", "--media", name, "--search", "test",
                                           "--before", "12", "--limit", "3", "--json"), 0)
                options = self.client.get_messages.await_args.kwargs
                self.assertIsInstance(options.pop("filter"), expected)
                self.assertEqual(options, dict(limit=3, search="test", offset_id=12))

    def test_invalid_new_argument_combinations_do_not_connect(self):
        path = self.directory / "file.bin"
        path.write_bytes(b"data")
        cases = [("read", "me", "--id", "1", "--search", "text"),
                 ("read", "me", "--id", "1", "--media", "photo"),
                 ("send", "me", "--attach", str(path), "--album"),
                 ("send", "me", "--attach", *([str(path)] * 11), "--album"),
                 ("send", "me", "--attach", str(path), "--caption", "a", "--caption", "b"),
                 ("download", "me", "1", "--output", str(path), "--album"),
                 ("download", "me", "1", "--output-dir", str(self.directory)),
                 ("download", "me", "1", "--output", str(self.directory / "missing" / "a")),
                 ("edit", "me", "1", " ")]
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual(self.main(*args), 1)
        self.factory.assert_not_called()

    def test_multi_file_send_preserves_order_and_captions(self):
        paths = [self.directory / "a.exe", self.directory / "b.apk", self.directory / "c.zip"]
        for number, path in enumerate(paths):
            path.write_bytes(bytes([number, 255]))
        seen = []
        async def upload(chat, stream, **options):
            seen.append((Path(stream.name), stream.read(), options["caption"]))
            return fake_message(42 + len(seen))
        self.client.send_file.side_effect = upload
        self.assertEqual(self.main("send", "me", "--attach", *map(str, paths), "--caption", "one",
                                   "--caption", "two", "--reply-to", "8", "--json"), 0)
        self.assertEqual(seen, [(paths[0], b"\x00\xff", "one"), (paths[1], b"\x01\xff", "two"),
                                (paths[2], b"\x02\xff", "")])
        self.assertEqual([item["id"] for item in json.loads(self.output.getvalue())["messages"]], [43, 44, 45])
        for invocation in self.client.send_file.await_args_list:
            self.assertTrue(invocation.args[1].closed)
            self.assertEqual(invocation.kwargs["reply_to"], 8)

    def test_album_send_uses_one_group_with_caption_per_file(self):
        paths = [self.directory / "a.jpg", self.directory / "b.jpg"]
        for path in paths:
            path.write_bytes(b"photo")
        async def upload(chat, streams, **options):
            self.assertEqual([Path(stream.name) for stream in streams], paths)
            self.assertEqual([stream.read() for stream in streams], [b"photo", b"photo"])
            return [fake_message(10, grouped_id=77), fake_message(11, grouped_id=77)]
        self.client.send_file.side_effect = upload
        self.assertEqual(self.main("send", "me", "--attach", *map(str, paths), "--album", "--caption", "first",
                                   "--caption", "<b>second</b>", "--format", "html", "--json"), 0)
        self.client.send_file.assert_awaited_once()
        self.assertEqual(self.client.send_file.await_args.kwargs["caption"], ["first", "<b>second</b>"])
        self.assertEqual(self.client.send_file.await_args.kwargs["parse_mode"], "html")
        self.assertTrue(all(stream.closed for stream in self.client.send_file.await_args.args[1]))
        self.assertEqual([item["grouped_id"] for item in json.loads(self.output.getvalue())["messages"]], [77, 77])

    def test_failed_multi_send_reports_completed_messages_without_retry(self):
        paths = [self.directory / "a.zip", self.directory / "b.zip"]
        for path in paths:
            path.write_bytes(b"data")
        self.client.send_file.side_effect = [fake_message(10), ConnectionError("offline")]
        self.assertEqual(self.main("send", "me", "--attach", *map(str, paths), "--json"), 1)
        result = json.loads(self.errors.getvalue())
        self.assertEqual(result["error"]["code"], "network_error")
        self.assertEqual([item["id"] for item in result["messages"]], [10])
        self.assertEqual(self.output.getvalue(), "")
        self.assertEqual(self.client.send_file.await_count, 2)
        self.client.disconnect.assert_awaited_once()

    def test_edit_text_file_and_stdin_with_formatting(self):
        path = self.directory / "caption.txt"
        path.write_text("<b>new</b>", encoding="utf-8-sig")
        self.client.get_messages.return_value = fake_message()
        for source in (["<b>new</b>"], ["--file", str(path)], ["--stdin"]):
            with self.subTest(source=source), io.TextIOWrapper(io.BytesIO(b"<b>new</b>")) as stream, \
                    patch("sys.stdin", stream):
                self.output.seek(0)
                self.output.truncate()
                self.assertEqual(self.main("edit", "me", "42", *source, "--format", "html", "--json"), 0)
                self.client.edit_message.assert_awaited_with("self", 42, "<b>new</b>", parse_mode="html")
                self.assertEqual(json.loads(self.output.getvalue())["message"]["id"], 42)

    def test_edit_rejects_missing_or_incoming_messages(self):
        for message, code in ((None, "message_not_found"), (fake_message(out=False), "not_outgoing")):
            self.errors.seek(0)
            self.errors.truncate()
            self.client.get_messages.return_value = message
            self.assertEqual(self.main("edit", "me", "42", "new", "--json"), 1)
            self.assertEqual(json.loads(self.errors.getvalue())["error"]["code"], code)
        self.client.edit_message.assert_not_awaited()

    def test_json_error_codes_retry_wait_and_interruption(self):
        for failure, code, exit_code in (
                (tg.FloodWaitError(request=None, capture=17), "flood_wait", 1),
                (tg.RPCError(request=None, message="denied", code=400), "rpc_error", 1),
                (ConnectionError("offline"), "network_error", 1),
                (sqlite3.OperationalError("locked"), "database_error", 1),
                (KeyboardInterrupt(), "cancelled", 130)):
            with self.subTest(code=code):
                self.errors.seek(0)
                self.errors.truncate()
                self.client.connect.side_effect = failure
                self.assertEqual(self.main("read", "me", "--json"), exit_code)
                result = json.loads(self.errors.getvalue())
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], code)
                self.assertEqual(result["error"]["retry_after"], 17 if code == "flood_wait" else None)
                if code == "rpc_error":
                    self.assertEqual(result["error"]["rpc_code"], 400)
                self.assertEqual(self.output.getvalue(), "")

    def test_configuration_and_usage_errors_are_json(self):
        with patch.dict(os.environ, {"TG_API_HASH": "invalid"}):
            self.assertEqual(self.main("send", "me", "text", "--json"), 1)
        self.assertEqual(json.loads(self.errors.getvalue())["error"]["code"], "configuration_error")
        self.errors.seek(0)
        self.errors.truncate()
        with self.assertRaises(SystemExit) as error:
            self.main("read", "me", "--limit", "0", "--json")
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(json.loads(self.errors.getvalue())["error"]["code"], "usage_error")
        self.factory.assert_not_called()

    def album(self):
        messages = [fake_message(number, grouped_id=77, file=SimpleNamespace(ext=extension))
                    for number, extension in ((10, ".zip"), (100, ".apk"), (200, "../../bad"))]
        self.client.get_messages.side_effect = [messages[1], [messages[0], fake_message(9)],
                                                [messages[2], fake_message(201)]]
        return messages

    def test_single_download_json_result(self):
        self.client.get_messages.return_value = fake_message(file=SimpleNamespace(ext=".zip"))
        async def download(message, file):
            file.write(b"\x00\xffzip")
            return file
        self.client.download_media.side_effect = download
        path = self.directory / "archive.zip"
        self.assertEqual(self.main("download", "me", "42", "--output", str(path), "--json"), 0)
        self.assertEqual(json.loads(self.output.getvalue()), {"ok": True, "files": [
            {"message_id": 42, "chat_id": 1, "path": str(path.resolve()), "text": "text", "grouped_id": None}]})
        self.assertEqual(path.read_bytes(), b"\x00\xffzip")

    def test_album_download_preserves_order_handles_id_gaps_and_safe_names(self):
        messages = self.album()
        seen = []
        async def download(message, file):
            seen.append(message.id)
            file.write(str(message.id).encode())
            return file
        self.client.download_media.side_effect = download
        self.assertEqual(self.main("download", "me", "100", "--album", "--output-dir", str(self.directory), "--json"), 0)
        self.assertEqual(seen, [10, 100, 200])
        self.assertEqual(self.client.get_messages.await_args_list, [call("self", ids=100),
            call("self", limit=10, offset_id=100), call("self", limit=10, min_id=100, reverse=True)])
        result = json.loads(self.output.getvalue())
        self.assertEqual([Path(item["path"]).name for item in result["files"]], ["10.zip", "100.apk", "200.bin"])
        for item in result["files"]:
            self.assertEqual(Path(item["path"]).read_bytes(), str(item["message_id"]).encode())

    def test_album_download_checks_all_collisions_before_writing(self):
        self.album()
        path = self.directory / "100.apk"
        path.write_bytes(b"original")
        self.assertEqual(self.main("download", "me", "100", "--album", "--output-dir", str(self.directory), "--json"), 1)
        self.assertEqual(json.loads(self.errors.getvalue())["error"]["code"], "file_exists")
        self.assertEqual(path.read_bytes(), b"original")
        self.assertEqual([item for item in self.directory.iterdir() if item.is_file()], [path])
        self.client.download_media.assert_not_awaited()

    def test_partial_album_download_reports_saved_files_and_removes_incomplete_file(self):
        self.album()
        async def download(message, file):
            file.write(b"data")
            if message.id == 100:
                raise ConnectionError("offline")
            return file
        self.client.download_media.side_effect = download
        self.assertEqual(self.main("download", "me", "100", "--album", "--output-dir", str(self.directory), "--json"), 1)
        result = json.loads(self.errors.getvalue())
        self.assertEqual(result["error"]["code"], "network_error")
        self.assertEqual([item["message_id"] for item in result["files"]], [10])
        self.assertEqual([item for item in self.directory.iterdir() if item.is_file()], [self.directory / "10.zip"])
        self.assertEqual(self.output.getvalue(), "")

    def test_album_download_rejects_non_album_messages(self):
        self.client.get_messages.return_value = fake_message(file=SimpleNamespace(ext=".zip"))
        self.assertEqual(self.main("download", "me", "42", "--album", "--output-dir", str(self.directory), "--json"), 1)
        self.assertEqual(json.loads(self.errors.getvalue())["error"]["code"], "not_an_album")
        self.client.download_media.assert_not_awaited()

    def test_filename_search_is_bounded_case_insensitive_and_limits_matches(self):
        self.client.get_messages.return_value = [fake_message(9), fake_message(8, file=fake_file(None)),
            fake_message(7, file=fake_file("app.APK")), fake_message(6, file=fake_file("app.zip")),
            fake_message(5, file=fake_file("old.apk")), fake_message(4, file=fake_file("older.apk"))]
        self.assertEqual(self.main("read", "me", "--filename", "*.apk", "--scan-limit", "6",
                                   "--limit", "2", "--before", "10", "--json"), 0)
        self.client.get_messages.assert_awaited_once_with("self", limit=6, offset_id=10)
        self.assertEqual([row["id"] for row in json.loads(self.output.getvalue())], [5, 7])

    def test_filename_search_defaults_and_empty_results(self):
        self.assertEqual(self.main("read", "me", "--filename", "report-?.[pz]*", "--json"), 0)
        self.client.get_messages.assert_awaited_once_with("self", limit=1000)
        self.assertEqual(json.loads(self.output.getvalue()), [])

    def test_filename_search_combines_with_telegram_filters_and_search(self):
        self.assertEqual(self.main("read", "me", "--filename", "*.zip", "--media", "document",
                                   "--search", "backup", "--json"), 0)
        options = self.client.get_messages.await_args.kwargs
        self.assertIsInstance(options["filter"], tg.MEDIA_FILTERS["document"])
        self.assertEqual(options["search"], "backup")
        self.assertEqual(options["limit"], 1000)

    def test_around_reads_neighbors_in_order_even_with_id_gaps(self):
        self.client.get_messages.side_effect = [fake_message(100), [fake_message(30), fake_message(10)],
                                                [fake_message(200), fake_message(400)]]
        self.assertEqual(self.main("read", "me", "--around", "100", "--context", "2", "--json"), 0)
        self.assertEqual([row["id"] for row in json.loads(self.output.getvalue())], [10, 30, 100, 200, 400])
        self.assertEqual(self.client.get_messages.await_args_list, [call("self", ids=100),
            call("self", limit=2, offset_id=100), call("self", limit=2, min_id=100, reverse=True)])

    def test_around_defaults_and_missing_target(self):
        self.client.get_messages.side_effect = [fake_message(1), [], []]
        self.assertEqual(self.main("read", "me", "--around", "1", "--json"), 0)
        self.assertEqual([row["id"] for row in json.loads(self.output.getvalue())], [1])
        self.assertEqual(self.client.get_messages.await_args.kwargs["limit"], 5)
        self.client.get_messages.side_effect = [None]
        self.assertEqual(self.main("read", "me", "--around", "2", "--json"), 1)
        self.assertEqual(json.loads(self.errors.getvalue())["error"]["code"], "message_not_found")

    def test_invalid_search_context_and_progress_arguments_do_not_connect(self):
        for args in (("read", "me", "--context", "5"), ("read", "me", "--scan-limit", "10"),
                     ("read", "me", "--filename", " "),
                     ("read", "me", "--around", "1", "--filename", "*.zip"),
                     ("read", "me", "--id", "1", "--filename", "*.apk"),
                     ("read", "me", "--around", "1", "--search", "text"),
                     ("send", "me", "text", "--progress")):
            with self.subTest(args=args):
                self.assertEqual(self.main(*args), 1)
        self.factory.assert_not_called()

    def test_forward_uses_requested_source_and_destination_without_file_transfer(self):
        self.client.get_input_entity.side_effect = ["source", "destination"]
        self.assertEqual(self.main("forward", "me", "123", "@recipient", "--json"), 0)
        self.client.forward_messages.assert_awaited_once_with("destination", 123, from_peer="source")
        self.assertEqual(self.client.get_input_entity.await_args_list, [call("me"), call("@recipient")])
        self.assertEqual(json.loads(self.output.getvalue())["messages"][0]["id"], 44)
        self.client.send_file.assert_not_awaited()
        self.client.download_media.assert_not_awaited()
        self.client.disconnect.assert_awaited_once()

    def test_forward_missing_protected_and_unauthorized_messages(self):
        for failure, code in ((None, "message_not_found"),
                              (tg.RPCError(request=None, message="CHAT_FORWARDS_RESTRICTED", code=400), "rpc_error")):
            with self.subTest(code=code):
                self.errors.seek(0)
                self.errors.truncate()
                self.client.forward_messages.reset_mock()
                self.client.forward_messages.return_value = None
                self.client.forward_messages.side_effect = failure
                self.assertEqual(self.main("forward", "me", "123", "@recipient", "--json"), 1)
                self.assertEqual(json.loads(self.errors.getvalue())["error"]["code"], code)
                self.assertEqual(self.client.forward_messages.await_count, 1)
        self.client.is_user_authorized.return_value = False
        self.client.forward_messages.reset_mock()
        self.assertEqual(self.main("forward", "me", "123", "@recipient", "--json"), 1)
        self.client.forward_messages.assert_not_awaited()

    def test_transfer_progress_keeps_json_stdout_clean(self):
        path = self.directory / "file.bin"
        path.write_bytes(b"data")
        async def upload(chat, stream, **options):
            options["progress_callback"](4, 4)
            return fake_message(43)
        self.client.send_file.side_effect = upload
        self.assertEqual(self.main("send", "me", "--attach", str(path), "--progress", "--json"), 0)
        self.assertTrue(json.loads(self.output.getvalue())["ok"])
        event = json.loads(self.errors.getvalue())
        self.assertEqual((event["event"], event["operation"], event["percent"]), ("transfer_progress", "upload", 100))
        self.assertEqual((event["transferred_bytes"], event["total_bytes"]), (4, 4))
        self.assertGreater(event["bytes_per_second"], 0)

    def test_download_progress_callback_and_opt_out(self):
        self.client.get_messages.return_value = fake_message(file=fake_file("file.bin"))
        async def download(message, file, **options):
            if "progress_callback" in options:
                options["progress_callback"](4, 4)
            file.write(b"data")
            return file
        self.client.download_media.side_effect = download
        for flag in ("--progress", "--no-progress"):
            self.errors.seek(0)
            self.errors.truncate()
            path = self.directory / (flag + ".bin")
            self.assertEqual(self.main("download", "me", "42", "--output", str(path), flag, "--json"), 0)
            self.assertEqual(path.read_bytes(), b"data")
            if flag == "--progress":
                self.assertEqual(json.loads(self.errors.getvalue())["operation"], "download")
            else:
                self.assertEqual(self.errors.getvalue(), "")


class ProgressTests(unittest.TestCase):
    def test_throttling_speed_completion_and_unknown_size(self):
        args = SimpleNamespace(progress=True, json=True)
        output = io.StringIO()
        with redirect_stderr(output), patch("tg.time.monotonic", side_effect=[0, 1, 1.1, 2, 3]):
            callback = tg.transfer_progress(args, "upload", "test.bin")
            callback(10, 100)
            callback(20, 100)
            callback(100, 100)
            callback(0, 0)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([event["transferred_bytes"] for event in events], [10, 100, 0])
        self.assertEqual([event["bytes_per_second"] for event in events], [10, 50, 0])
        self.assertEqual([event["percent"] for event in events], [10, 100, None])

    def test_album_fractional_progress_is_weighted_by_file_size(self):
        args = SimpleNamespace(progress=True, json=True)
        output = io.StringIO()
        with redirect_stderr(output), patch("tg.time.monotonic", side_effect=[0, 1, 2, 3]):
            callback = tg.transfer_progress(args, "upload", "album", [10, 90])
            callback(0.5, 2)
            callback(1.5, 2)
            callback(2, 2)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([event["transferred_bytes"] for event in events], [5, 55, 100])
        self.assertEqual([event["percent"] for event in events], [5, 55, 100])

    def test_terminal_default_opt_out_and_sanitized_text(self):
        args = SimpleNamespace(progress=None, json=False)
        with patch("tg.sys.stderr.isatty", return_value=False):
            self.assertIsNone(tg.transfer_progress(args, "upload", "test"))
        with patch("tg.sys.stderr.isatty", return_value=True), patch("builtins.print") as output:
            callback = tg.transfer_progress(args, "upload", "test\x1b\u202e.bin")
            callback(100, 100)
            self.assertNotIn("\x1b", output.call_args.args[0])
            self.assertNotIn("\u202e", output.call_args.args[0])
            self.assertIn("100.0%", output.call_args.args[0])
            self.assertIn("bytes/s", output.call_args.args[0])
            args.progress = False
            self.assertIsNone(tg.transfer_progress(args, "upload", "test"))


class MediaTests(unittest.IsolatedAsyncioTestCase):
    def message(self):
        from telethon.tl.types import Message, MessageMediaDocument, Document, DocumentAttributeFilename
        return Message(id=7, peer_id=None, date=datetime(2026, 1, 1, tzinfo=timezone.utc), message="caption",
                       media=MessageMediaDocument(document=Document(
                           id=1, access_hash=2, file_reference=b"", date=datetime.now(timezone.utc),
                           mime_type="application/pdf", size=4, dc_id=2,
                           attributes=[DocumentAttributeFilename("../report\x1b\u202e.pdf")])))

    async def test_media_metadata_and_sanitized_display(self):
        message = self.message()
        metadata = tg.message_data(message)["media"]
        self.assertEqual(metadata, dict(type="document", name="../report\x1b\u202e.pdf",
                         mime_type="application/pdf", size=4, ext=".pdf", width=None, height=None, duration=None))
        client = FakeClient()
        client.get_messages.return_value = [message]
        output = io.StringIO()
        with redirect_stdout(output):
            await tg.run(client, tg.parser().parse_args(["read", "me"]))
        self.assertIn("caption", output.getvalue())
        self.assertIn("document, ../report.pdf, application/pdf, 4 bytes", output.getvalue())
        self.assertNotIn("\x1b", output.getvalue())
        self.assertNotIn("\u202e", output.getvalue())

    async def test_photo_metadata_without_filename_and_service_message(self):
        from telethon.tl.types import Message, MessageMediaPhoto, Photo, PhotoSize
        message = Message(id=8, peer_id=None, message="", date=datetime.now(timezone.utc), media=MessageMediaPhoto(
            photo=Photo(id=1, access_hash=2, file_reference=b"", date=datetime.now(timezone.utc),
                        sizes=[PhotoSize(type="x", w=100, h=50, size=20)], dc_id=2)))
        data = tg.media_data(message)
        self.assertEqual((data["type"], data["name"], data["width"], data["height"]), ("photo", None, 100, 50))
        with patch("builtins.print") as output:
            tg.show_message(message)
        self.assertIn("photo, image/jpeg, 20 bytes", output.call_args.args[0])
        self.assertIsNone(tg.media_data(SimpleNamespace(media=None)))

    async def test_download_bytes_uses_explicit_path_and_never_overwrites(self):
        client = FakeClient()
        message = self.message()
        client.get_messages.return_value = message
        async def download(message, file):
            file.write(b"data")
            return file
        client.download_media.side_effect = download
        with TemporaryDirectory() as directory:
            path = Path(directory) / "chosen.pdf"
            args = tg.parser().parse_args(["download", "me", "7", "--output", str(path)])
            with patch("builtins.print"):
                await tg.run(client, args)
            self.assertEqual(path.read_bytes(), b"data")
            client.get_messages.assert_awaited_once_with("self", ids=7)
            with self.assertRaises(FileExistsError):
                await tg.run(client, args)
            self.assertEqual(path.read_bytes(), b"data")
            client.download_media.assert_awaited_once()
            self.assertEqual(list(Path(directory).iterdir()), [path])

    async def test_failed_or_cancelled_download_removes_partial_file(self):
        import asyncio
        for failure in (OSError("offline"), asyncio.CancelledError(), None):
            client = FakeClient()
            client.get_messages.return_value = self.message()
            async def download(message, file):
                file.write(b"partial")
                if failure is not None:
                    raise failure
                return None
            client.download_media.side_effect = download
            with TemporaryDirectory() as directory:
                path = Path(directory) / "file.pdf"
                args = tg.parser().parse_args(["download", "me", "7", "--output", str(path)])
                with self.assertRaises(type(failure) if failure is not None else ValueError):
                    await tg.run(client, args)
                self.assertFalse(path.exists())
            client.disconnect.assert_awaited_once()

    async def test_missing_message_or_attachment_creates_nothing(self):
        for message in (None, SimpleNamespace(file=None)):
            client = FakeClient()
            client.get_messages.return_value = message
            with TemporaryDirectory() as directory:
                path = Path(directory) / "file.bin"
                with self.assertRaises(ValueError):
                    await tg.run(client, tg.parser().parse_args(["download", "me", "1", "--output", str(path)]))
                self.assertFalse(path.exists())
            client.download_media.assert_not_awaited()
            client.disconnect.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
