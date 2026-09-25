"""Turn Telethon objects into plain records matching the store's tables."""

from __future__ import annotations

from typing import Any

from telethon import types, utils

from ..values import to_iso


def peer_record(entity: Any) -> dict[str, Any]:
    return {
        "id": utils.get_peer_id(entity),
        "type": type(entity).__name__,
        "username": getattr(entity, "username", None),
        "name": utils.get_display_name(entity) or None,
    }


def _file_attribute(file: Any, name: str) -> Any:
    # Telethon computes some of these (e.g. photo width) and may fail on odd media.
    try:
        return getattr(file, name, None)
    except (TypeError, ValueError):
        return None


# Telethon Message properties, checked in order: stickers, voice notes, GIFs... are all
# documents, and a venue is also a geo point.
_MEDIA_KINDS = (
    ("sticker", "sticker"),
    ("video_note", "video_note"),
    ("gif", "gif"),
    ("voice", "voice"),
    ("video", "video"),
    ("audio", "audio"),
    ("photo", "photo"),
    ("document", "file"),
    ("geo", "location"),
    ("contact", "contact"),
    ("poll", "poll"),
    ("dice", "dice"),
    ("game", "game"),
    ("invoice", "invoice"),
)


def media_kind(message: Any, media_type: str) -> str | None:
    """photo, video, sticker, voice, file, ...; None for a link preview (a text message)."""
    if media_type == "WebPage":  # Telethon also exposes a preview's photo as `.photo`
        return None
    for attribute, kind in _MEDIA_KINDS:
        if getattr(message, attribute, None):
            return kind
    return media_type.lower()


def media_record(message: Any) -> dict[str, Any] | None:
    media = getattr(message, "media", None)
    if media is None:
        return None
    media_class = type(media).__name__
    media_type = media_class.removeprefix("MessageMedia") or media_class
    file = getattr(message, "file", None)
    source = getattr(file, "media", None)  # the underlying Photo or Document
    source_id = getattr(source, "id", None)
    return {
        "type": media_type,
        "kind": media_kind(message, media_type),
        "file_key": f"{type(source).__name__.lower()}:{source_id}" if source_id else None,
        **{
            key: _file_attribute(file, attribute)
            for key, attribute in (
                ("name", "name"),
                ("extension", "ext"),
                ("mime_type", "mime_type"),
                ("size", "size"),
                ("width", "width"),
                ("height", "height"),
                ("duration", "duration"),
            )
        },
    }


def _forward(header: Any) -> dict[str, Any] | None:
    if header is None:
        return None
    return {
        "from_id": utils.get_peer_id(header.from_id) if header.from_id else None,
        "from_name": header.from_name,
        "date": to_iso(header.date),
        "channel_post": header.channel_post,
        "post_author": header.post_author,
    }


def _reactions(reactions: Any) -> list[dict[str, Any]] | None:
    results = getattr(reactions, "results", None)
    if not results:
        return None
    return [
        {
            "reaction": getattr(item.reaction, "emoticon", None)
            or getattr(item.reaction, "document_id", None)
            or type(item.reaction).__name__,
            "count": item.count,
        }
        for item in results
    ]


def _link_preview(media: Any) -> dict[str, Any] | None:
    webpage = getattr(media, "webpage", None)
    if not isinstance(webpage, types.WebPage):
        return None
    return {"url": webpage.url, "title": webpage.title, "site_name": webpage.site_name}


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        value = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in value.items() if item not in (None, "", [], {}, False)}
    return value


def meta_record(message: Any) -> dict[str, Any]:
    """Telegram fields without a column of their own, kept as JSON in ``messages.meta``."""
    entities = getattr(message, "entities", None) or []
    replies = getattr(message, "replies", None)
    return _drop_empty(
        {
            "forward": _forward(getattr(message, "fwd_from", None)),
            "via_bot_id": getattr(message, "via_bot_id", None),
            "post_author": getattr(message, "post_author", None),
            "pinned": getattr(message, "pinned", None),
            "views": getattr(message, "views", None),
            "forwards": getattr(message, "forwards", None),
            "replies": getattr(replies, "replies", None),
            "reactions": _reactions(getattr(message, "reactions", None)),
            # Hidden links behind text are lost in raw_text.
            "links": [e.url for e in entities if isinstance(e, types.MessageEntityTextUrl)],
            "link_preview": _link_preview(getattr(message, "media", None)),
            "sticker_emoji": _file_attribute(getattr(message, "file", None), "emoji")
            if getattr(message, "sticker", None)
            else None,
        }
    )


def message_record(message: Any) -> dict[str, Any]:
    """Columns of the ``messages`` table plus nested ``sender`` and ``media`` records."""
    sender = getattr(message, "sender", None)
    action = getattr(message, "action", None)
    return {
        "id": message.id,
        "date": to_iso(getattr(message, "date", None)),
        "edit_date": to_iso(getattr(message, "edit_date", None)),
        "sender_id": getattr(message, "sender_id", None),
        "sender": peer_record(sender) if sender is not None else None,
        "outgoing": bool(getattr(message, "out", False)),
        "text": getattr(message, "raw_text", None) or "",
        "reply_to_message_id": getattr(message, "reply_to_msg_id", None),
        "grouped_id": getattr(message, "grouped_id", None),
        "service_action": type(action).__name__ if action is not None else None,
        "media": media_record(message),
        "meta": meta_record(message),
    }
