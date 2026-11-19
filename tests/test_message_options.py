import io
import json
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch

from telethon import events, functions, types
from telethon.extensions.binaryreader import BinaryReader

import tg
from tcli_messages import markdown_v2, parse_html, read_wire, wire_value
from test_tg import FakeClient, fake_message


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
PEER = types.InputPeerUser(1, 2)


class ProtocolClient(tg.UploadClient):
    """Serialize real Telethon requests and return synthetic server updates."""

    def __init__(self):
        super().__init__(None, 123, "synthetic")
        self.requests = []
        self.bot = False
        self.original = types.Message(7, types.PeerUser(1), NOW, "old", out=True)
        self.short_result = False

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def is_user_authorized(self):
        return True

    async def is_bot(self):
        return self.bot

    async def get_input_entity(self, entity):
        return PEER

    async def get_messages(self, entity, *, ids):
        return self.original

    async def upload_file(self, file, **options):
        return types.InputFile(10, 1, Path(getattr(file, "name", file)).name, "synthetic")

    async def __call__(self, request):
        with BinaryReader(bytes(request)) as reader:
            self.requests.append(reader.tgread_object())
        if isinstance(request, functions.messages.UploadMediaRequest):
            if isinstance(request.media, types.InputMediaUploadedDocument):
                document = types.Document(12, 6, b"ref", NOW, request.media.mime_type, 30, 1, request.media.attributes)
                return types.MessageMediaDocument(document=document)
            return types.MessageMediaPhoto(photo=types.Photo(11, 6, b"ref", NOW, [], 1))
        if isinstance(request, functions.messages.EditMessageRequest):
            edited = types.Message(request.id, types.PeerUser(1), NOW, request.message, out=True)
            return types.UpdateShort(types.UpdateEditMessage(edited, 1, 1), NOW)
        if self.short_result:
            return types.UpdateShortSentMessage(80, 1, 1, NOW, out=True)
        items = getattr(request, "multi_media", [request])
        updates = []
        for index, item in enumerate(items, 80):
            msg = types.Message(index, types.PeerUser(1), NOW, item.message, out=True,
                                entities=item.entities)
            updates.extend([types.UpdateMessageID(index, item.random_id), types.UpdateNewMessage(msg, 1, 1)])
        return types.Updates(updates, [], [], NOW, 1)


class FormattingTests(TestCase):
    def test_markdown_v2_offsets_nesting_escaping_and_every_style(self):
        text, entities = markdown_v2(
            "😀 *bold _italic_* __under__ ~gone~ ||hidden|| \\! \x60a.b\x60 "
            "\x60\x60\x60python\nprint(1)\n\x60\x60\x60 [site](https://example.com/a\\)) "
            "![x](tg://emoji?id=123) ![today](tg://time?unix=1647531900&format=wDT)\n"
            ">one\n>two\n**>expand||")
        self.assertEqual(text, "😀 bold italic under gone hidden ! a.b print(1)\n site x today\none\ntwo\nexpand")
        self.assertEqual([type(entity) for entity in entities], [
            types.MessageEntityBold, types.MessageEntityItalic, types.MessageEntityUnderline,
            types.MessageEntityStrike, types.MessageEntitySpoiler, types.MessageEntityCode,
            types.MessageEntityPre, types.MessageEntityTextUrl, types.MessageEntityCustomEmoji,
            types.MessageEntityFormattedDate, types.MessageEntityBlockquote, types.MessageEntityBlockquote])
        self.assertEqual(entities[0].offset, 3)
        self.assertEqual(entities[6].language, "python")
        self.assertEqual(entities[7].url, "https://example.com/a)")
        self.assertTrue(entities[9].day_of_week)
        self.assertTrue(entities[-1].collapsed)
        _, entities = markdown_v2(">quote with ||spoiler||")
        self.assertFalse(entities[0].collapsed)
        self.assertIsInstance(entities[1], types.MessageEntitySpoiler)
        for malformed in ("open *bold", "[link]", "> > nested!", "unescaped.", "ends with \\"):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                markdown_v2(malformed)

    def test_html_formatting_spoiler_date_and_expanding_quote(self):
        text, entities = parse_html(
            '<b>😀<br/><tg-spoiler>hide</tg-spoiler></b><span class="tg-spoiler">x</span>'
            '<ins>under</ins><strike>strike</strike><blockquote expandable>quote</blockquote>'
            '<tg-time unix="1647531900" format="r">date</tg-time><tg-emoji emoji-id="42">y</tg-emoji>')
        self.assertEqual(text, "😀\nhidexunderstrikequotedatey")
        self.assertEqual([type(e) for e in entities], [
            types.MessageEntityBold, types.MessageEntitySpoiler, types.MessageEntitySpoiler,
            types.MessageEntityUnderline, types.MessageEntityStrike, types.MessageEntityBlockquote,
            types.MessageEntityFormattedDate, types.MessageEntityCustomEmoji])
        self.assertEqual(entities[1].offset, 3)
        self.assertTrue(entities[5].collapsed)
        self.assertTrue(entities[6].relative)

    def test_schema_rejects_bad_types_and_accepts_lists_bytes_and_dates(self):
        geo = {"_": "InputGeoPoint", "lat": 1, "long": 2}
        self.assertEqual(read_wire(geo, types.TypeInputGeoPoint).lat, 1.0)
        entity = types.MessageEntityFormattedDate(1, 3, NOW, short_time=True)
        self.assertEqual(read_wire(wire_value(entity), types.TypeMessageEntity), entity)
        for invalid in ({"_": "MessageEntityBold", "offset": True, "length": 2},
                        {"_": "MessageEntityBold", "offset": 1, "length": 2, "unknown": 1},
                        {"_": "InputMediaDice", "emoticon": "x"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                read_wire(invalid, types.TypeMessageEntity)


class MessageOptionTests(IsolatedAsyncioTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.client = ProtocolClient()

    def data_file(self, content):
        path = self.directory / "data.json"
        path.write_text(json.dumps(wire_value(content)), encoding="utf-8")
        return str(path)

    def media_file(self, name):
        path = self.directory / name
        path.write_bytes(b"synthetic attachment")
        return str(path)

    async def invoke(self, *command):
        args = tg.parser().parse_args([*command, "--json"])
        if args.command in ("send", "edit"):
            tg.prepare_message_options(args)
        output = io.StringIO()
        with redirect_stdout(output):
            await tg.run(self.client, args)
        return json.loads(output.getvalue()), self.client.requests[-1]

    async def test_interactive_media_round_trips_through_real_send_requests(self):
        def text(value):
            return types.TextWithEntities(value, [])

        point = types.InputGeoPoint(10.5, -11.2)
        options = [types.InputPollAnswer(text("Yes")), types.InputPollAnswer(text("No"))]
        poll = types.Poll(0, text("Question?"), options, 0)
        quiz = types.Poll(0, text("Quiz?"), options, 0, quiz=True)
        samples = [
            types.InputMediaPoll(poll),
            types.InputMediaPoll(quiz, correct_answers=[0], solution="Yes", solution_entities=[]),
            types.InputMediaContact("+12025550123", "Test", "Contact", ""),
            types.InputMediaGeoPoint(point),
            types.InputMediaGeoLive(point, period=120),
            types.InputMediaVenue(point, "Venue", "Address", "provider", "id", ""),
            types.InputMediaDice("🎲"),
            types.InputMediaGame(types.InputGameID(1, 2)),
            types.InputMediaInvoice("Invoice", "Test", types.Invoice("XTR", [types.LabeledPrice("Test", 3)]),
                                    b"\x00\xff", types.DataJSON("{}")),
            types.InputMediaTodo(types.TodoList(text("List"), [types.TodoItem(1, text("Item"))])),
            types.InputMediaStory(PEER, 3),
            types.InputMediaPaidMedia(2, [types.InputMediaPhoto(types.InputPhoto(1, 2, b"ref"))]),
            types.InputMediaDocument(types.InputDocument(1, 2, b"ref")),
            types.InputMediaPhotoExternal("https://example.com/image.jpg"),
            types.InputMediaDocumentExternal("https://example.com/document.pdf"),
        ]
        for sample in samples:
            with self.subTest(sample=type(sample).__name__):
                data, request = await self.invoke("send", "me", "--media-json", self.data_file(sample),
                                                  "--silent", "--reply-to", "7")
                self.assertIsInstance(request, functions.messages.SendMediaRequest)
                self.assertEqual(bytes(request.media), bytes(sample))
                self.assertTrue(request.silent)
                self.assertEqual(request.reply_to.reply_to_msg_id, 7)
                self.assertEqual(data["messages"][0]["id"], 80)

    async def test_buttons_rich_messages_and_link_preview_modes(self):
        buttons = types.ReplyInlineMarkup([
            types.KeyboardInlineButtonRow([
                types.KeyboardInlineButton("Open", types.InlineButtonTypeUrl("https://example.com"))])])
        self.client.bot = True
        self.client.short_result = True
        data, request = await self.invoke("send", "@sample", "button", "--buttons-json", self.data_file(buttons))
        self.assertEqual(bytes(request.reply_markup), bytes(buttons))
        self.assertIn("reply_markup", data["messages"][0])
        rich = [types.InputRichMessageHTML("<h1>Hello</h1>"), types.InputRichMessageMarkdown("# Hello"),
                types.InputRichMessage([types.PageBlockParagraph(types.TextPlain("Hello"))])]
        for sample in rich:
            _, request = await self.invoke("send", "@sample", "--rich-json", self.data_file(sample))
            self.assertEqual(bytes(request.rich_message), bytes(sample))
            _, request = await self.invoke("edit", "@sample", "7", "--rich-json", self.data_file(sample))
            self.assertEqual(bytes(request.rich_message), bytes(sample))
        for mode in ("off", "auto", "above", "large", "small"):
            with self.subTest(mode=mode):
                extra = ["--preview-url", "https://example.com"] if mode in ("large", "small") else []
                _, request = await self.invoke("send", "@sample", "https://example.com", "--link-preview", mode, *extra)
                if extra:
                    self.assertEqual(bool(request.media.force_large_media), mode == "large")
                    self.assertEqual(bool(request.media.force_small_media), mode == "small")
                else:
                    self.assertEqual(bool(request.no_webpage), mode == "off")
                    self.assertEqual(bool(request.invert_media), mode == "above")
        _, request = await self.invoke("edit", "@sample", "7", "Preview", "--preview-url", "https://example.com",
                                       "--link-preview", "small", "--preview-above")
        self.assertTrue(request.media.force_small_media)
        self.assertTrue(request.invert_media)

    async def test_entities_offsets_and_edit_clear_replace(self):
        entities = [types.MessageEntityTextUrl(3, 4, "https://example.com")]
        data, request = await self.invoke("send", "me", "😀 link", "--entities", self.data_file(entities))
        self.assertEqual(request.entities, entities)
        self.assertEqual(data["messages"][0]["entities"], wire_value(entities))
        with self.assertRaisesRegex(ValueError, "UTF-16"):
            await self.invoke("send", "me", "😀 link", "--entities", self.data_file([types.MessageEntityBold(1, 1)]))
        photo = types.Photo(1, 2, b"ref", NOW, [], 1)
        self.client.original.media = types.MessageMediaPhoto(photo=photo)
        _, cleared = await self.invoke("edit", "me", "7", "--clear-caption")
        self.assertEqual(cleared.message, "")
        _, replacement = await self.invoke("edit", "me", "7", "--attach", self.media_file("new.jpg"),
                                            "--caption", "||hidden||", "--format", "markdownv2")
        self.assertIsInstance(replacement.media, types.InputMediaUploadedPhoto)
        self.assertEqual(replacement.message, "hidden")
        self.assertIsInstance(replacement.entities[0], types.MessageEntitySpoiler)
        _, replacement = await self.invoke("edit", "me", "7", "--media-json",
                                            self.data_file(types.InputMediaPhoto(types.InputPhoto(1, 2, b"ref"))))
        self.assertIsNone(replacement.message)

    async def test_attachment_types_and_options(self):
        for kind, name, flags, attr, attribute_flag in (
                ("photo", "file.webp", [], None, None),
                ("document", "file.jpg", [], types.DocumentAttributeFilename, None),
                ("video", "file.mp4", ["--streaming", "--duration", "5", "--dimensions", "320", "240"],
                 types.DocumentAttributeVideo, "supports_streaming"),
                ("audio", "file.mp3", ["--duration", "4"], types.DocumentAttributeAudio, None),
                ("voice", "file.ogg", ["--duration", "3"], types.DocumentAttributeAudio, "voice"),
                ("video-note", "file.mp4", [], types.DocumentAttributeVideo, "round_message"),
                ("animation", "file.gif", [], types.DocumentAttributeAnimated, None),
                ("sticker", "file.webp", ["--sticker-emoji", "🙂", "--dimensions", "512", "512"],
                 types.DocumentAttributeSticker, None)):
            with self.subTest(kind=kind):
                _, request = await self.invoke("send", "me", "--attach", self.media_file(name),
                                               "--attachment-type", kind, *flags)
                if attr:
                    self.assertIsInstance(request.media, types.InputMediaUploadedDocument)
                    selected = next(item for item in request.media.attributes if isinstance(item, attr))
                    if attribute_flag:
                        self.assertTrue(getattr(selected, attribute_flag))
                else:
                    self.assertIsInstance(request.media, types.InputMediaUploadedPhoto)
        _, request = await self.invoke("send", "me", "--attach", self.media_file("file.mp4"),
                                       "--cover", self.media_file("cover.jpg"), "--thumbnail", self.media_file("thumb.jpg"),
                                       "--view-once", "--media-spoiler")
        self.assertEqual(request.media.ttl_seconds, 0x7fffffff)
        self.assertTrue(request.media.spoiler)
        self.assertEqual((request.media.video_cover.id, request.media.thumb.name), (11, "thumb.jpg"))
        _, request = await self.invoke("send", "me", "--attach", self.media_file("photo.jpg"), "--ttl", "15")
        self.assertEqual(request.media.ttl_seconds, 15)
        _, request = await self.invoke("send", "me", "--attach", self.media_file("photo.jpg"), "--as-file")
        self.assertIsInstance(request.media, types.InputMediaUploadedDocument)

    async def test_album_retains_per_caption_entities_and_spoilers(self):
        files = [self.media_file("first.jpg"), self.media_file("second.jpg")]
        data, request = await self.invoke("send", "me", "--attach", *files, "--album", "--media-spoiler",
                                          "--caption", "<b>one</b>", "--caption", "||two||",
                                          "--caption-format", "html", "--caption-format", "markdownv2")
        self.assertEqual([item["id"] for item in data["messages"]], [80, 81])
        first, second = request.multi_media
        self.assertTrue(first.media.spoiler and second.media.spoiler)
        self.assertEqual((first.message, second.message), ("one", "two"))
        self.assertIsInstance(first.entities[0], types.MessageEntityBold)
        self.assertIsInstance(second.entities[0], types.MessageEntitySpoiler)
        groups = [[types.MessageEntityItalic(0, 3)], [types.MessageEntityUnderline(0, 3)]]
        _, request = await self.invoke("send", "me", "--attach", *files, "--album", "--caption", "one",
                                       "--caption", "two", "--entities", self.data_file(groups))
        self.assertEqual([media.entities for media in request.multi_media], groups)
        videos = [self.media_file("first.mp4"), self.media_file("second.mp4")]
        _, request = await self.invoke("send", "me", "--attach", *videos, "--album",
                                       "--cover", self.media_file("cover.jpg"))
        self.assertEqual([item.media.video_cover.id for item in request.multi_media], [11, 11])

    def test_invalid_new_options_fail_before_client_creation(self):
        photo = self.media_file("image.jpg")
        audio = self.media_file("audio.ogg")
        bad_json = self.data_file({"_": "MessageEntityBold"})
        cases = (
            ("send", "me", "--media-json", bad_json),
            ("send", "me", "omit escape.", "--format", "markdownv2"),
            ("send", "me", "--attach", photo, audio, "--caption", "plain", "--caption", "malformed!",
             "--caption-format", "plain", "--caption-format", "markdownv2"),
            ("send", "me", "--attach", photo, "--attachment-type", "sticker"),
            ("send", "me", "--attach", photo, "--thumbnail", photo),
            ("send", "me", "--attach", audio, "--attachment-type", "voice", "--duration", "2147483648"),
            ("send", "me", "text", "--link-preview", "large"),
            ("send", "me", "text", "--link-preview", "off", "--preview-above"),
            ("forward", "me", "1", "2", "me", "--album"),
        )
        with patch.object(tg, "UploadClient") as client:
            for command in cases:
                with self.subTest(command=command), patch.object(sys, "argv", ["tcli", *command, "--json"]):
                    with io.TextIOWrapper(io.BytesIO(), encoding="utf-8") as error, redirect_stderr(error):
                        self.assertEqual(tg.main(), 1)
                        error.seek(0)
                        self.assertEqual(json.load(error)["error"]["code"], "invalid_input")
            client.assert_not_called()

    async def test_jsonl_preserves_payloads_service_and_sticker_filter(self):
        sticker = types.Document(1, 2, b"ref", NOW, "video/webm", 12, 1, [
            types.DocumentAttributeVideo(1, 512, 512),
            types.DocumentAttributeSticker("🙂", types.InputStickerSetEmpty())])
        video_sticker = types.Message(4, types.PeerUser(1), NOW, "",
                                      media=types.MessageMediaDocument(document=sticker))
        dice = types.Message(3, types.PeerUser(1), NOW, "", media=types.MessageMediaDice(4, "🎲"))
        service = types.MessageService(2, types.PeerUser(1), NOW, action=types.MessageActionChatAddUser([3]))
        markup = types.Message(1, types.PeerUser(1), NOW, "link",
                               entities=[types.MessageEntityTextUrl(0, 4, "https://example.com")])
        history = [video_sticker, dice, service, markup]
        client = FakeClient()
        async def items(*args, **options):
            for message in history:
                yield message
        client.iter_messages = items
        self.client = client
        output = self.directory / "history.jsonl"
        args = tg.parser().parse_args(["export", "me", "--output", str(output), "--format", "jsonl"])
        with redirect_stdout(io.StringIO()):
            await tg.run(client, args)
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["entities"][0]["url"], "https://example.com")
        self.assertEqual(rows[1]["service"]["users"], [3])
        self.assertEqual(rows[2]["media_payload"]["value"], 4)
        self.assertEqual(rows[3]["media"]["type"], "sticker")
        for kind, expected in (("sticker", 4), ("dice", 3), ("service", 2), ("text", 1)):
            with self.subTest(kind=kind):
                args = tg.parser().parse_args(["read", "me", "--media", kind, "--json"])
                output = io.StringIO()
                with redirect_stdout(output):
                    await tg.run(client, args)
                self.assertEqual([row["id"] for row in json.loads(output.getvalue())], [expected])

    async def test_forward_multiple_ids_and_album_and_bot_service_watch(self):
        client = FakeClient()
        client.forward_messages.return_value = [fake_message(51), fake_message(52)]
        items = [fake_message(20, grouped_id=5), fake_message(21, grouped_id=5)]
        client.get_messages.side_effect = [items[0], [], [items[1]]]
        for options in (["20", "21", "me"], ["20", "me", "--album"]):
            args = tg.parser().parse_args(["forward", "me", *options, "--json"])
            with redirect_stdout(io.StringIO()):
                await tg.run(client, args)
            client.forward_messages.assert_awaited_with("self", [20, 21], from_peer="self")
        client.is_bot.return_value = True
        client.get_input_entity.return_value = PEER
        client.add_event_handler = Mock()
        client.remove_event_handler = Mock()
        client.session = Mock()
        async def updates():
            handler = next(call.args[0] for call in client.add_event_handler.call_args_list
                           if isinstance(call.args[1], events.Raw))
            for peer_id in (2, 1):
                service = types.MessageService(2, types.PeerUser(peer_id), NOW, action=types.MessageActionChatAddUser([3]))
                await handler(types.UpdateNewMessage(service, 1, 1))
        client.run_until_disconnected = updates
        args = tg.parser().parse_args(["watch", "@sample", "--service", "--json"])
        output = io.StringIO()
        with redirect_stdout(output):
            await tg.run(client, args)
        self.assertEqual(json.loads(output.getvalue())["service"]["users"], [3])
        self.assertEqual(client.remove_event_handler.call_count, 2)
