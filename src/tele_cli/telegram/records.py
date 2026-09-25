"""Turn Telethon objects into plain records matching the store's tables."""

from __future__ import annotations

from typing import Any

from telethon import utils

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


def media_record(message: Any) -> dict[str, Any] | None:
    media = getattr(message, "media", None)
    if media is None:
        return None
    media_class = type(media).__name__
    file = getattr(message, "file", None)
    source = getattr(file, "media", None)  # the underlying Photo or Document
    source_id = getattr(source, "id", None)
    return {
        "type": media_class.removeprefix("MessageMedia") or media_class,
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
    }
