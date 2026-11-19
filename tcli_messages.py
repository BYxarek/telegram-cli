"""Telegram message entities and media used by the command-line interface."""

import base64
from datetime import datetime, timezone
import inspect
import re
import struct
from typing import get_args, get_origin, get_type_hints, Union
from urllib.parse import parse_qs, urlsplit

from telethon import functions, helpers, types, utils
from telethon.extensions import html
from telethon.tl.tlobject import TLObject


def wire_value(value):
    """Turn a Telegram value into JSON data, retaining constructor names."""
    if isinstance(value, TLObject):
        fields = value.to_dict()
        return {key: fields[key] if key == "_" else wire_value(getattr(value, key))
                for key in fields}
    if isinstance(value, dict):
        return {key: wire_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [wire_value(item) for item in value]
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def read_wire(value, expected):
    """Decode only constructors permitted by the expected Telegram schema type."""
    origin = get_origin(expected)
    if origin is Union:
        choices = get_args(expected)
        if isinstance(value, dict):
            for constructor in choices:
                if constructor.__name__ == value.get("_"):
                    return read_wire(value, constructor)
        else:
            for choice in choices:
                try:
                    return read_wire(value, choice)
                except ValueError:
                    pass
    elif origin is list and isinstance(value, list):
        return [read_wire(item, get_args(expected)[0]) for item in value]
    elif inspect.isclass(expected) and issubclass(expected, TLObject):
        if isinstance(value, dict) and value.get("_") == expected.__name__:
            fields = get_type_hints(expected.__init__)
            try:
                return expected(**{key: read_wire(item, fields[key])
                                   for key, item in value.items() if key != "_"})
            except (KeyError, TypeError) as error:
                raise ValueError(f"Invalid fields for {expected.__name__}: {error}") from error
    elif expected is bytes and isinstance(value, str):
        return base64.b64decode(value, validate=True)
    elif expected is datetime and isinstance(value, str):
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.utcoffset() is not None:
            return date
    elif expected is float and type(value) in (int, float):
        if float("-inf") < value < float("inf"):
            return float(value)
    elif type(value) is expected:
        return value
    raise ValueError(f"Invalid Telegram JSON value for {getattr(expected, '__name__', str(expected))}.")


def entity_list(value):
    if not isinstance(value, list):
        raise ValueError("Formatting entities must be a JSON array.")
    return [read_wire(item, types.TypeMessageEntity) for item in value]


def validate_entities(text, entities):
    boundaries = {0}
    position = 0
    for char in text:
        position += 2 if ord(char) > 0xffff else 1
        boundaries.add(position)
    for entity in entities:
        if (entity.offset < 0 or entity.length <= 0 or entity.offset not in boundaries
                or entity.offset + entity.length not in boundaries):
            raise ValueError("Formatting entities must cover whole characters using UTF-16 offsets.")
        try:
            bytes(entity)
        except (AssertionError, OverflowError, struct.error) as error:
            raise ValueError(f"Invalid formatting entity: {error}") from error


DATE_FORMATS = {"r": "relative", "t": "short_time", "T": "long_time",
                "d": "short_date", "D": "long_date", "w": "day_of_week"}


def date_entity(offset, length, value, style):
    try:
        date = (datetime.fromtimestamp(int(value), timezone.utc)
                if value.isdecimal() else read_wire(value, datetime))
        flags = {DATE_FORMATS[item]: True for item in style}
    except (ValueError, OverflowError, OSError, KeyError) as error:
        raise ValueError("Invalid formatted date or date format.") from error
    return types.MessageEntityFormattedDate(offset, length, date, **flags)


class TelegramHTML(html.HTMLToTelegramParser):
    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.handle_data("\n")
            return
        if tag == "tg-spoiler" or (tag == "span" and "tg-spoiler" in dict(attrs).get("class", "").split()):
            super().handle_starttag(tag, attrs)
            self._building_entities[tag] = types.MessageEntitySpoiler(len(self.text), 0)
            return
        if tag == "tg-time" or (tag == "time" and "datetime" in dict(attrs)):
            values = dict(attrs)
            super().handle_starttag(tag, attrs)
            self._building_entities[tag] = date_entity(
                len(self.text), 0, values.get("unix", values.get("datetime", "")), values.get("format", ""))
            return
        super().handle_starttag({"strike": "s", "ins": "u"}.get(tag, tag), attrs)

    def handle_endtag(self, tag):
        if tag != "br":
            super().handle_endtag({"strike": "s", "ins": "u"}.get(tag, tag))


def parse_html(text):
    parser = TelegramHTML()
    parser.feed(helpers.add_surrogate(text))
    parser.close()
    text = helpers.strip_text(parser.text, parser.entities)
    return helpers.del_surrogate(text), sorted(reversed(parser.entities), key=lambda entity: entity.offset)


MD2_STYLES = {"*": types.MessageEntityBold, "_": types.MessageEntityItalic,
              "__": types.MessageEntityUnderline, "~": types.MessageEntityStrike,
              "||": types.MessageEntitySpoiler}
MD2_TOKEN = re.compile(r"\\[\x01-\x7e]|\\|\x60{3}|\x60|__|\|\||[*_~]|!?\[|\]|[>()#=+\-{}.!|]|\r")
MD2_LINK = re.compile(r"\((?:\\.|[^)\\])*\)")


def markdown_v2(text):
    # Work in UTF-16 code units, as required by Telegram offsets.
    source = helpers.add_surrogate(text.replace("\r\n", "\n"))
    output, entities, stack = [], [], []
    position = 0
    length = 0
    while position < len(source):
        match = MD2_TOKEN.search(source, position)
        end = match.start() if match else len(source)
        literal = source[position:end]
        output.append(literal)
        length += len(literal)
        if match is None:
            break
        token = match[0]
        position = match.end()
        if token.startswith("\\") and len(token) == 2:
            output.append(token[1:])
            length += 1
        elif token == "\r":
            continue
        elif token == ">" and (match.start() == 0 or source[match.start() - 1] == "\n"
                               or source[:match.start()] == "**" or
                               source[match.start() - 3:match.start()] == "\n**"):
            stop = source.find("\n", position)
            stop = len(source) if stop < 0 else stop
            # Quote lines share a single entity, including Telegram's expandable suffix.
            while source[stop:stop + 2] == "\n>":
                next_stop = source.find("\n", stop + 2)
                stop = len(source) if next_stop < 0 else next_stop
            quoted = source[position:stop]
            quoted = quoted.replace("\n>", "\n")
            if quoted.startswith(">") or "\n>" in quoted:
                raise ValueError("MarkdownV2 block quotes cannot be nested.")
            collapsed = False
            try:
                rendered, nested = markdown_v2(helpers.del_surrogate(quoted))
            except ValueError:
                if not quoted.endswith("||"):
                    raise
                # A dangling spoiler marker at the end of a quote signals expansion.
                rendered, nested = markdown_v2(helpers.del_surrogate(quoted[:-2]))
                collapsed = True
            rendered = helpers.add_surrogate(rendered)
            if rendered:
                entities.append(types.MessageEntityBlockquote(length, len(rendered), collapsed or None))
            for entity in nested:
                entity.offset += length
                entities.append(entity)
            output.append(rendered)
            length += len(rendered)
            position = stop
        elif token in ("`", "```"):
            closing = re.search(r"(?<!\\)(?:\\\\)*(" + re.escape(token) + ")", source[position:])
            if closing is None:
                raise ValueError("Unclosed code in MarkdownV2.")
            code = source[position:position + closing.start(1)]
            position += closing.end(1)
            code = re.sub(r"\\([\\`])", r"\1", code)
            if token == "```":
                language, newline, body = code.partition("\n")
                if newline and re.fullmatch(r"[\w+.#-]*", language):
                    code = body
                else:
                    language = ""
                entity = types.MessageEntityPre(length, len(code), language)
            else:
                entity = types.MessageEntityCode(length, len(code))
            entities.append(entity)
            length += len(code)
            output.append(code)
        elif token in MD2_STYLES:
            if stack and stack[-1][0] == token:
                _, offset = stack.pop()
                entities.append(MD2_STYLES[token](offset, length - offset))
            else:
                stack.append((token, length))
        elif token in ("[", "!["):
            stack.append((token, length))
        elif token == "]" and stack and stack[-1][0] in ("[", "!["):
            link = MD2_LINK.match(source, position)
            if link is None:
                raise ValueError("MarkdownV2 links need a destination.")
            kind, offset = stack.pop()
            url = helpers.del_surrogate(re.sub(r"\\([)\\])", r"\1", link[0][1:-1]))
            if kind == "![":
                emoji = re.fullmatch(r"tg://emoji\?id=(\d+)", url)
                if emoji is not None:
                    entities.append(types.MessageEntityCustomEmoji(offset, length - offset, int(emoji[1])))
                elif url.startswith("tg://time?"):
                    params = parse_qs(urlsplit(url).query)
                    entities.append(date_entity(offset, length - offset, params.get("unix", [""])[0],
                                                params.get("format", [""])[0]))
                else:
                    raise ValueError("MarkdownV2 image syntax requires a Telegram emoji or date link.")
            else:
                entities.append(types.MessageEntityTextUrl(offset, length - offset, url))
            position = link.end()
        else:
            raise ValueError(f"Escape {token!r} in MarkdownV2.")
    if stack:
        raise ValueError(f"Unclosed {stack[-1][0]!r} in MarkdownV2.")
    rendered = helpers.strip_text("".join(output), entities)
    return helpers.del_surrogate(rendered), sorted((entity for entity in entities if entity.length),
                                                    key=lambda entity: (entity.offset, -entity.length))


def parse_mode(mode, text):
    if mode == "plain":
        return None
    if mode == "markdownv2":
        return markdown_v2
    if mode == "html" and re.search(r"<(?:br|tg-spoiler|tg-time|span|time|strike|ins)\b", text, re.I):
        return parse_html
    return mode


async def attachment_media(client, peer, stream, args, progress=None):
    kind = args.attachment_type
    attributes = []
    if kind == "audio" and args.duration is not None:
        attributes.append(types.DocumentAttributeAudio(args.duration))
    if kind == "voice" and args.duration is not None:
        attributes.append(types.DocumentAttributeAudio(args.duration, voice=True))
    if kind in ("video", "video-note", "animation") and (args.dimensions or args.duration is not None):
        width, height = args.dimensions or (1, 1)
        attributes.append(types.DocumentAttributeVideo(
            args.duration or 0, width, height, round_message=kind == "video-note" or None,
            supports_streaming=args.streaming or None))
    if kind == "animation":
        attributes.append(types.DocumentAttributeAnimated())
    if kind == "sticker":
        attributes.append(types.DocumentAttributeSticker(args.sticker_emoji, types.InputStickerSetEmpty()))
        if args.dimensions is not None:
            attributes.append(types.DocumentAttributeImageSize(*args.dimensions))
    _, media, _ = await client._file_to_media(
        stream, force_document=args.as_file or kind == "document", as_image=kind == "photo" if kind else None,
        attributes=attributes, voice_note=kind == "voice", video_note=kind == "video-note",
        supports_streaming=args.streaming, thumb=args.thumbnail, ttl=args.ttl, progress_callback=progress)
    if not isinstance(media, (types.InputMediaUploadedPhoto, types.InputMediaUploadedDocument)):
        raise ValueError("The local attachment could not be prepared.")
    if kind == "audio" and not any(isinstance(item, types.DocumentAttributeAudio) for item in media.attributes):
        media.attributes.append(types.DocumentAttributeAudio(0))
    if args.media_spoiler:
        media.spoiler = True
    if args.cover is not None:
        with args.cover.open("rb") as cover:
            uploaded = await client.upload_file(cover)
        result = await client(functions.messages.UploadMediaRequest(peer, types.InputMediaUploadedPhoto(uploaded)))
        media.video_cover = utils.get_input_photo(result.photo)
    return media
