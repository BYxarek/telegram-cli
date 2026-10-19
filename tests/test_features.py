"""Offline contract tests for history, metadata, scheduling and safe transfers."""
import asyncio
import argparse
import io
import json
import sqlite3
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import tg
import test_tg as fixtures
from test_tg import FakeClient, fake_message, fake_file


def history(client, messages):
    async def iterate(*args, **kwargs):
        for message in messages:
            yield message
    client.iter_messages = Mock(side_effect=iterate)


class NewMainTests(unittest.TestCase):
    setUp = fixtures.MainTests.setUp
    main = fixtures.MainTests.main

    def test_capabilities_offline(self):
        with patch("tg.load_credentials", side_effect=AssertionError("Must be offline")):
            self.assertEqual(self.main("capabilities"), 0)
        data = json.loads(self.output.getvalue())
        self.assertIn("topics", data["commands"])
        self.assertIn("install", data["commands"]["skills"]["commands"])
        self.assertIn("outputs", data)
        self.factory.assert_not_called()
        self.assertFalse((self.directory / "telegram-cli").exists())

    def test_link_reads_exact_message(self):
        self.client.get_messages.return_value = fake_message(12)
        self.assertEqual(self.main("read", "https://t.me/example/12", "--json"), 0)
        self.client.get_input_entity.assert_awaited_with("@example")
        self.client.get_messages.assert_awaited_with("self", ids=12)

    def test_validation_happens_before_connection(self):
        cases = [
            ["read", "me", "--since", "2026-02-02", "--until", "2026-02-01"],
            ["read", "me", "--after", "20", "--before", "10"],
            ["read", "me", "--id", "1", "--after", "1"],
            ["read", "me", "--json", "--jsonl"],
            ["read", "me", "--jsonl", "--id", "1"],
            ["read", "me", "--topic", "1", "--search", "hello"],
            ["read", "https://t.me/example/12", "--id", "12"],
            ["topics", "me", "--before-topic", "1"],
            ["send", "me", "hi", "--schedule", "2020-01-01T00:00:00Z"],
            ["download", "me", "--batch", "--output-dir", str(self.directory)],
            ["download", "me", "1", "--output", str(self.directory / "a"), "--search", "x"],
            ["download", "me", "1", "--batch", "--max-bytes", "100", "--output-dir", str(self.directory)],
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual(self.main(*args), 1)
        self.factory.assert_not_called()

    def test_lock_diagnostic_preserves_session(self):
        session = self.directory / "telegram-cli" / "account.session"
        session.parent.mkdir()
        session.write_bytes(b"keep")
        self.client.connect.side_effect = sqlite3.OperationalError("database is locked")
        self.assertEqual(self.main("read", "me", "--json"), 1)
        error = json.loads(self.errors.getvalue())["error"]
        self.assertEqual(error["code"], "database_error")
        self.assertIn("Do not delete", error["message"])
        self.assertEqual(session.read_bytes(), b"keep")

    def test_schedule_and_silent(self):
        value = "2099-01-02T12:00:00+03:00"
        self.assertEqual(self.main("send", "me", "text", "--silent", "--schedule", value), 0)
        self.client.send_message.assert_awaited_once_with("self", "text", parse_mode=None,
                                                        silent=True, schedule=tg.timestamp(value))
        self.assertIn("scheduled", self.output.getvalue())

    def test_jsonl_main_sets_machine_errors_and_streams(self):
        history(self.client, [fake_message(1), fake_message(2)])
        self.assertEqual(self.main("read", "me", "--jsonl"), 0)
        self.assertEqual([json.loads(row)["id"] for row in self.output.getvalue().splitlines()], [1, 2])


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_numeric_chat_resolution_stops_after_unique_id_match(self):
        client = FakeClient()
        async def dialogs():
            yield SimpleNamespace(id=-1001, input_entity="found")
            raise AssertionError("Must not fetch further dialog pages after matching a unique ID")
        client.iter_dialogs = dialogs
        self.assertEqual(await tg.resolve_chat(client, "-1001"), "found")

    async def execute(self, client, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            await tg.run(client, tg.parser().parse_args(arguments))
        return output.getvalue(), errors.getvalue()

    def test_timestamps_and_links(self):
        self.assertEqual(tg.timestamp("2026-01-01").tzinfo, timezone.utc)
        self.assertEqual(tg.timestamp("2026-01-01T00:00:00Z").hour, 0)
        for value in ("2026-01-01T12:00:00", "nonsense"):
            with self.assertRaises(argparse.ArgumentTypeError):
                tg.timestamp(value)
        self.assertEqual(tg.parse_message_link("https://t.me/c/123/45"), ("-1000000000123", 45))
        for value in ("http://t.me/name/1", "https://t.me.evil/name/1", "https://t.me/+invite", "https://t.me/n/0"):
            with self.assertRaises(ValueError):
                tg.parse_message_link(value)

    async def test_sender_and_id_filters(self):
        client = FakeClient()
        await self.execute(client, "read", "me", "--after", "10", "--before", "50", "--from-user", "123", "--json")
        client.get_input_entity.assert_awaited_with(123)
        client.get_messages.assert_awaited_once_with("self", limit=20, min_id=10, offset_id=50, from_user="self")

    async def test_date_boundaries_and_latest_order(self):
        client = FakeClient()
        messages = [fake_message(day, date=datetime(2026, 1, day, tzinfo=timezone.utc)) for day in (5, 4, 3, 2, 1)]
        history(client, messages)
        out, _ = await self.execute(client, "read", "me", "--since", "2026-01-02", "--until", "2026-01-05", "--json")
        self.assertEqual([m["id"] for m in json.loads(out)], [2, 3, 4])
        self.assertEqual(client.iter_messages.call_args.kwargs["offset_date"], tg.timestamp("2026-01-05"))

    async def test_jsonl_streams_ascending_with_bounds(self):
        client = FakeClient()
        history(client, [fake_message(i, date=datetime(2026, 1, i, tzinfo=timezone.utc)) for i in range(1, 6)])
        out, _ = await self.execute(client, "read", "me", "--jsonl", "--after", "2", "--before", "8",
                                    "--since", "2026-01-02", "--until", "2026-01-04")
        self.assertEqual([json.loads(line)["id"] for line in out.splitlines()], [2, 3])
        self.assertEqual(client.iter_messages.call_args.kwargs, dict(limit=None, reverse=True, min_id=2, max_id=8))

    async def test_stream_emits_before_fetching_next_message(self):
        client, output = FakeClient(), io.StringIO()
        async def iterator(*args, **kwargs):
            yield fake_message(1)
            self.assertEqual(json.loads(output.getvalue())["id"], 1)
            raise OSError("offline")
        client.iter_messages = iterator
        with redirect_stdout(output), self.assertRaises(OSError):
            await tg.run(client, tg.parser().parse_args(["read", "me", "--jsonl"]))
        client.disconnect.assert_awaited_once()

    async def test_chat_search_limit_counts_matches(self):
        client = FakeClient([SimpleNamespace(id=i, name=name, unread_count=0) for i, name in
                             enumerate(("Other", "WORK one", "Work two", "work three"))])
        out, _ = await self.execute(client, "chats", "--search", "work", "--limit", "2", "--json")
        self.assertEqual([m["name"] for m in json.loads(out)], ["WORK one", "Work two"])

    async def test_scan_limit_warning_only_when_incomplete(self):
        client = FakeClient()
        client.get_messages.return_value = [fake_message(2), fake_message(1)]
        out, err = await self.execute(client, "read", "me", "--filename", "*.zip", "--scan-limit", "2", "--json")
        self.assertEqual(json.loads(out), [])
        self.assertEqual(json.loads(err)["event"], "scan_limit_reached")
        client.get_messages.return_value = [fake_message(2, file=fake_file("a.zip"))]
        _, err = await self.execute(client, "read", "me", "--filename", "*.zip", "--scan-limit", "1", "--limit", "1", "--json")
        self.assertEqual(err, "")

    async def test_topic_and_pinned_filters(self):
        client = FakeClient()
        client.get_entity = AsyncMock(return_value=SimpleNamespace(forum=True))
        await self.execute(client, "read", "me", "--topic", "12", "--json")
        client.get_messages.assert_awaited_with("self", limit=20, reply_to=12)
        await self.execute(client, "pinned", "me", "--json")
        self.assertIsInstance(client.get_messages.await_args.kwargs["filter"], tg.types.InputMessagesFilterPinned)

    async def test_general_topic_excludes_other_topics_and_starters(self):
        client = FakeClient()
        client.get_entity = AsyncMock(return_value=SimpleNamespace(forum=True))
        history(client, [fake_message(6, reply_to=SimpleNamespace(forum_topic=True, reply_to_top_id=1)),
                         fake_message(5, reply_to=SimpleNamespace(forum_topic=True, reply_to_top_id=4)),
                         fake_message(4, action=tg.types.MessageActionTopicCreate(title="Other", icon_color=0)),
                         fake_message(3, reply_to=SimpleNamespace(forum_topic=False))])
        out, _ = await self.execute(client, "read", "me", "--topic", "1", "--json")
        self.assertEqual([item["id"] for item in json.loads(out)], [3, 6])
        self.assertNotIn("reply_to", client.iter_messages.call_args.kwargs)
        client.get_entity.return_value.forum = False
        with self.assertRaisesRegex(ValueError, "forum"):
            await self.execute(client, "read", "me", "--topic", "1", "--json")

    def test_sender_name_keeps_numeric_id_and_sanitizes_display(self):
        message = fake_message(out=False, sender=tg.types.User(id=1, first_name="Ada\x1b", last_name="L"))
        self.assertEqual(tg.message_data(message)["sender_name"], "Ada\x1b L")
        out = io.StringIO()
        with redirect_stdout(out):
            tg.show_message(message)
        self.assertIn("Ada L (1)", out.getvalue())
        self.assertNotIn("\x1b", out.getvalue())

    def test_export_links_only_address_channel_messages(self):
        self.assertIsNone(tg.message_link(fake_message(chat=tg.types.User(id=1, username="ada"))))
        channel = tg.types.Channel(id=1, title="Channel", photo=tg.types.ChatPhotoEmpty(),
                                   date=datetime.now(timezone.utc), username="channel")
        self.assertEqual(tg.message_link(fake_message(2, chat_id=-1000000000001, chat=channel)), "https://t.me/channel/2")

    async def test_thread_missing_parent_and_depth(self):
        client = FakeClient()
        client.get_messages.side_effect = [fake_message(3, reply_to_msg_id=2), fake_message(2, reply_to_msg_id=1), None]
        out, err = await self.execute(client, "thread", "me", "3", "--json")
        self.assertEqual([m["id"] for m in json.loads(out)], [2, 3])
        self.assertEqual(json.loads(err)["reason"], "missing_parent")
        client.get_messages.side_effect = [fake_message(3, reply_to_msg_id=2)]
        _, err = await self.execute(client, "thread", "me", "3", "--depth", "1", "--json")
        self.assertEqual(json.loads(err)["reason"], "depth_limit")

    async def test_thread_root_and_cross_chat_guard(self):
        client = FakeClient()
        client.get_messages.return_value = fake_message(3)
        _, err = await self.execute(client, "thread", "me", "3", "--json")
        self.assertEqual(err, "")
        client.get_messages.return_value = fake_message(3, reply_to_msg_id=2, reply_to=SimpleNamespace(reply_to_peer_id=tg.types.PeerChannel(99)))
        _, err = await self.execute(client, "thread", "me", "3", "--json")
        self.assertEqual(json.loads(err)["reason"], "cross_chat_parent")
        client.get_messages.return_value = None
        with self.assertRaises(tg.CommandError):
            await self.execute(client, "thread", "me", "3", "--json")

    async def test_export_markdown_and_no_overwrite(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.md"
            client = FakeClient()
            history(client, [fake_message(2, chat_id=-1000000000123, raw_text="# remote heading\nbody"), fake_message(1)])
            out, _ = await self.execute(client, "export", "me", "--output", str(path), "--json")
            self.assertEqual(json.loads(out)["count"], 2)
            text = path.read_text(encoding="utf-8")
            self.assertIn("https://t.me/c/123/2", text)
            self.assertIn("> # remote heading", text)
            self.assertLess(text.index("#1"), text.index("#2"))
            with self.assertRaises(FileExistsError):
                await self.execute(client, "export", "me", "--output", str(path))
            self.assertEqual(path.read_text(encoding="utf-8"), text)

    async def test_watch_edits_and_handler_cleanup(self):
        client = FakeClient()
        client.add_event_handler = Mock()
        client.remove_event_handler = Mock()
        client.is_connected.return_value = True
        client.get_messages.side_effect = [[], [fake_message(2)], []]
        async def pause(_):
            await client.add_event_handler.call_args.args[0](SimpleNamespace(message=fake_message(1, raw_text="edited")))
            client.is_connected.return_value = False
        with patch("tg.asyncio.sleep", side_effect=pause):
            out, _ = await self.execute(client, "watch", "me", "--edits", "--json")
        self.assertEqual([json.loads(line)["event"] for line in out.splitlines()], ["new", "edit"])
        client.remove_event_handler.assert_called_once_with(*client.add_event_handler.call_args.args)

    async def test_watch_polls_edits_without_server_events_and_deduplicates(self):
        client = FakeClient()
        client.add_event_handler = Mock()
        client.remove_event_handler = Mock()
        client.is_connected.side_effect = [True, True, False]
        older = fake_message(49)
        original = fake_message(50, raw_text="original")
        changed = fake_message(50, raw_text="changed", edit_date=datetime.now(timezone.utc))
        new = fake_message(51)
        client.get_messages.side_effect = [[original, older], [new, changed, older], [new, changed, older]]
        async def pause(_):
            await client.add_event_handler.call_args.args[0](SimpleNamespace(message=changed))
        with patch("tg.asyncio.sleep", side_effect=pause):
            out, _ = await self.execute(client, "watch", "me", "--edits", "--json")
        rows = [json.loads(line) for line in out.splitlines()]
        self.assertEqual([(row["event"], row["id"]) for row in rows], [("edit", 50), ("new", 51)])
        self.assertEqual(rows[0]["text"], "changed")

    async def test_watch_edit_polling_window_stays_bounded(self):
        client = FakeClient()
        client.add_event_handler = Mock()
        client.remove_event_handler = Mock()
        client.is_connected.side_effect = [True, True, False]
        original = [fake_message(i) for i in range(200, 100, -1)]
        recent = [fake_message(i) for i in range(205, 105, -1)]
        client.get_messages.side_effect = [original, recent, recent]
        with patch("tg.asyncio.sleep", new_callable=AsyncMock):
            out, _ = await self.execute(client, "watch", "me", "--edits", "--json")
        self.assertEqual([json.loads(line)["id"] for line in out.splitlines()], list(range(201, 206)))
        self.assertEqual(client.get_messages.await_args_list, [call("self", limit=100)] * 3)

    async def test_watch_edit_polling_recovers_bursts_larger_than_window(self):
        client = FakeClient()
        client.add_event_handler = Mock()
        client.remove_event_handler = Mock()
        client.is_connected.side_effect = [True, False]
        recent = [fake_message(i) for i in range(300, 200, -1)]
        missed = [fake_message(i) for i in range(101, 201)]
        client.get_messages.side_effect = [[fake_message(100)], recent, missed]
        with patch("tg.asyncio.sleep", new_callable=AsyncMock):
            out, _ = await self.execute(client, "watch", "me", "--edits", "--json")
        self.assertEqual([json.loads(line)["id"] for line in out.splitlines()], list(range(101, 301)))
        client.get_messages.assert_awaited_with("self", min_id=100, max_id=201, reverse=True, limit=None)


class RpcClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.rpc = AsyncMock()
        self.get_entity = AsyncMock()
        self.get_permissions = AsyncMock()

    async def __call__(self, request):
        return await self.rpc(request)


class RpcTests(unittest.IsolatedAsyncioTestCase):
    execute = HistoryTests.execute
    # Keep RPC tests separate from fixtures for normal high-level Telethon calls.
    async def test_topics_request_and_forum_validation(self):
        client = RpcClient()
        client.get_entity.return_value = SimpleNamespace(forum=False)
        with self.assertRaisesRegex(ValueError, "not a forum"):
            await self.execute(client, "topics", "me", "--json")
        client.get_entity.return_value.forum = True
        topic = tg.types.ForumTopic(id=12, date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            peer=tg.types.PeerChannel(1), title="Plans", icon_color=0, top_message=50,
            read_inbox_max_id=0, read_outbox_max_id=0, unread_count=2, unread_mentions_count=0,
            unread_reactions_count=0, unread_poll_votes_count=0, from_id=tg.types.PeerUser(1),
            notify_settings=tg.types.PeerNotifySettings())
        latest_date = datetime(2026, 2, 1, tzinfo=timezone.utc)
        client.rpc.return_value = SimpleNamespace(topics=[topic], messages=[fake_message(50, date=latest_date)], order_by_create_date=False)
        out, _ = await self.execute(client, "topics", "me", "--json")
        self.assertEqual(json.loads(out)[0]["id"], 12)
        self.assertEqual(json.loads(out)[0]["offset_date"], latest_date.isoformat())
        self.assertIsInstance(client.rpc.await_args.args[0], tg.functions.messages.GetForumTopicsRequest)
        client.rpc.return_value.order_by_create_date = True
        out, _ = await self.execute(client, "topics", "me", "--json", "--before-topic", "12",
                                    "--offset-id", "50", "--offset-date", latest_date.isoformat())
        self.assertEqual(json.loads(out)[0]["offset_date"], topic.date.isoformat())

    async def test_scheduled_cancel_validates_all_ids(self):
        client = RpcClient()
        client.get_messages.return_value = [fake_message(7)]
        out, _ = await self.execute(client, "scheduled", "me", "--json")
        self.assertEqual(json.loads(out)[0]["id"], 7)
        with self.assertRaises(tg.CommandError):
            await self.execute(client, "scheduled", "me", "--cancel", "7", "8", "--json")
        client.rpc.assert_not_awaited()
        out, _ = await self.execute(client, "scheduled", "me", "--cancel", "7", "--json")
        self.assertEqual(json.loads(out)["cancelled_ids"], [7])
        request = client.rpc.await_args.args[0]
        self.assertIsInstance(request, tg.functions.messages.DeleteScheduledMessagesRequest)
        self.assertEqual(request.id, [7])

    async def test_info_user_and_restricted_group(self):
        client = RpcClient()
        client.get_entity.return_value = tg.types.User(id=1, first_name="Ada", username="ada")
        client.rpc.return_value = SimpleNamespace(full_user=SimpleNamespace(about="About"))
        out, _ = await self.execute(client, "info", "me", "--json")
        self.assertEqual(json.loads(out)["description"], "About")
        client.get_permissions.assert_not_awaited()
        client.get_entity.return_value = tg.types.Channel(id=2, title="Group", photo=tg.types.ChatPhotoEmpty(),
            date=datetime.now(timezone.utc), megagroup=True, default_banned_rights=tg.types.ChatBannedRights(
                until_date=None, send_messages=True))
        client.rpc.return_value = SimpleNamespace(full_chat=SimpleNamespace(about="Group about"))
        client.get_permissions.return_value = SimpleNamespace(is_admin=False)
        out, _ = await self.execute(client, "info", "me", "--json")
        self.assertFalse(json.loads(out)["permissions"]["send_messages"])
        client.get_entity.return_value.megagroup = False
        client.get_permissions.return_value = SimpleNamespace(is_admin=True, post_messages=True)
        out, _ = await self.execute(client, "info", "me", "--json")
        self.assertEqual(json.loads(out)["type"], "channel")
        self.assertTrue(json.loads(out)["permissions"]["send_messages"])
        client.get_entity.return_value = tg.types.Chat(id=3, title="Basic", photo=tg.types.ChatPhotoEmpty(),
                                                       participants_count=2, date=datetime.now(timezone.utc), version=1)
        client.get_permissions.return_value = SimpleNamespace(is_admin=False)
        out, _ = await self.execute(client, "info", "me", "--json")
        self.assertTrue(json.loads(out)["permissions"]["send_messages"])
        self.assertIsInstance(client.rpc.await_args.args[0], tg.functions.messages.GetFullChatRequest)


class TransferTests(unittest.IsolatedAsyncioTestCase):
    execute = HistoryTests.execute

    async def test_batch_limits_and_preflight_before_writes(self):
        with TemporaryDirectory() as directory:
            client = FakeClient()
            history(client, [fake_message(4), fake_message(3, file=fake_file("a.zip")), fake_message(2, file=fake_file("b.zip"))])
            async def download(message, file, **kwargs):
                file.write(b"x")
                return file
            client.download_media.side_effect = download
            with self.assertRaisesRegex(ValueError, "exceed"):
                await self.execute(client, "download", "me", "--batch", "--output-dir", directory,
                                   "--max-bytes", "1", "--limit", "2", "--json")
            client.download_media.assert_not_awaited()
            self.assertEqual(list(Path(directory).iterdir()), [])
            out, _ = await self.execute(client, "download", "me", "--batch", "--output-dir", directory,
                                        "--max-bytes", "2", "--limit", "2", "--json")
            files = json.loads(out)["files"]
            self.assertEqual([item["message_id"] for item in files], [3, 2])
            self.assertEqual((Path(directory) / "3.bin").read_bytes(), b"x")

    async def test_batch_collision_prevents_all_downloads(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "1.bin"
            path.write_bytes(b"keep")
            client = FakeClient()
            history(client, [fake_message(2, file=fake_file("a")), fake_message(1, file=fake_file("b"))])
            with self.assertRaises(FileExistsError):
                await self.execute(client, "download", "me", "--batch", "--output-dir", directory, "--max-bytes", "2")
            client.download_media.assert_not_awaited()
            self.assertEqual(path.read_bytes(), b"keep")

    async def test_resume_verifies_hash_and_finishes_from_offset(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "out.bin"
            file = fake_file("out.bin")
            file.size = 6
            message = fake_message(1, file=file, media="media", document=SimpleNamespace(id=99))
            client = FakeClient()
            class Iterator:
                def __init__(self, fail):
                    self.fail = fail
                    self.close = AsyncMock()
                async def __aiter__(self):
                    yield b"abc" if self.fail else b"def"
                    if self.fail:
                        raise OSError("offline")
            first, second = Iterator(True), Iterator(False)
            client.iter_download = Mock(side_effect=[first, second])
            with self.assertRaises(OSError):
                await tg.resume_attachment(client, message, path)
            part = path.with_name("out.bin.part")
            self.assertEqual(part.read_bytes(), b"abc")
            self.assertFalse(path.exists())
            self.assertFalse(path.with_name("out.bin.part.lock").exists())
            part.write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError, "checksum"):
                await tg.resume_attachment(client, message, path)
            self.assertEqual(client.iter_download.call_count, 1)
            part.write_bytes(b"abc")
            result = await tg.resume_attachment(client, message, path)
            self.assertEqual(client.iter_download.call_args.kwargs["offset"], 3)
            self.assertEqual(path.read_bytes(), b"abcdef")
            self.assertEqual(result["path"], str(path.resolve()))
            self.assertEqual(list(Path(directory).iterdir()), [path])
            first.close.assert_awaited_once()
            second.close.assert_awaited_once()
            with self.assertRaises(FileExistsError):
                await tg.resume_attachment(client, message, path)

    async def test_resume_rejects_changed_media_and_lock(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "out.bin"
            file = fake_file("a")
            file.size = 3
            message = fake_message(1, file=file, media="media", document=SimpleNamespace(id=99))
            client = FakeClient()
            class Iterator:
                close = AsyncMock()
                async def __aiter__(self):
                    yield b"a"
                    raise asyncio.CancelledError()
            client.iter_download = Mock(return_value=Iterator())
            with self.assertRaises(asyncio.CancelledError):
                await tg.resume_attachment(client, message, path)
            self.assertEqual(path.with_name("out.bin.part").read_bytes(), b"a")
            message.document.id = 100
            with self.assertRaisesRegex(ValueError, "does not match"):
                await tg.resume_attachment(client, message, path)
            lock = path.with_name("out.bin.part.lock")
            lock.touch()
            with self.assertRaises(tg.CommandError) as error:
                await tg.resume_attachment(client, message, path)
            self.assertEqual(error.exception.error_code, "download_locked")
            self.assertTrue(lock.exists())

    async def test_resume_final_collision_preserves_both_files(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "out.bin"
            message = fake_message(1, file=fake_file("a"), media="media", document=SimpleNamespace(id=1))
            class Iterator:
                close = AsyncMock()
                async def __aiter__(self):
                    path.write_bytes(b"keep")
                    yield b"a"
            client = FakeClient()
            client.iter_download = Mock(return_value=Iterator())
            with self.assertRaises(FileExistsError):
                await tg.resume_attachment(client, message, path)
            self.assertEqual(path.read_bytes(), b"keep")
            self.assertEqual(path.with_name("out.bin.part").read_bytes(), b"a")
