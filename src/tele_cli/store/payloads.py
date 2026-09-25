"""Convert store rows into the compact JSON shapes printed by `tele` and `tele-local`.

Empty values are left out so long conversations stay short for agents to read.
"""

from __future__ import annotations

import json
import os
import re
from datetime import tzinfo
from typing import Any

from ..config import Node, display_timezone
from ..values import display_time

# Why an attachment has no file on this machine; the short message form reports these too.
MISSING_FILE_STATUSES = frozenset({"skipped_too_large", "error", "file_missing"})

# Pictographs and symbols, plus the joiners, variation selectors, tags and keycaps that
# combine them into one emoji. Digits, "#" and "*" only count inside a keycap.
_EMOJI_ONLY = re.compile(
    "(?:[\U0001f000-\U0001faff\u2600-\u27bf\u2300-\u23ff\u2b00-\u2bff\u2194-\u2199\u21a9\u21aa"
    "\u25a0-\u25ff\u2934\u2935\u3030\u303d\u3297\u3299\u00a9\u00ae\u203c\u2049\u2122\u2139"
    "\u24c2\u200d\ufe0f\U000e0020-\U000e007f]|[0-9#*]\ufe0f?\u20e3|\\s)+"
)


def compact(mapping: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None and value != ""}


def peer_payload(row: Any) -> dict[str, Any]:
    return compact(
        {"id": row["id"], "type": row["type"], "username": row["username"], "name": row["name"]}
    )


def media_payload(row: Any, files: list[Any], node: Node) -> dict[str, Any]:
    """Media columns selected with the ``media_`` prefix, plus every stored copy.

    ``files`` lists which machine (hostname + OS user) keeps a copy and where;
    ``local_path`` is the copy on this machine, when it is still on disk.
    """
    media = compact(
        {
            "type": row["media_kind"] or row["media_type"],
            "name": row["media_name"],
            "mime_type": row["media_mime_type"],
            "size": row["media_size"],
            "width": row["media_width"],
            "height": row["media_height"],
            "duration": row["media_duration"],
            "download_status": row["media_download_status"],
            "download_error": row["media_download_error"],
        }
    )
    here = [f["path"] for f in files if (f["host"], f["os_user"]) == (node.host, node.user)]
    if here:
        if os.path.isfile(here[0]):
            media["local_path"] = here[0]
        else:
            media["download_status"] = "file_missing"
    if files:
        media["files"] = [
            {"host": f["host"], "user": f["os_user"], "path": f["path"]} for f in files
        ]
    return media


def _sender_label(row: Any) -> str | int | None:
    return row["sender_name"] or row["sender_username"] or row["sender_id"]


def message_type(row: Any) -> str:
    """The attachment kind (photo, video, sticker, voice, file, ...), else service/emoji/text."""
    if row["media_kind"]:
        return row["media_kind"]
    if row["service_action"]:
        return "service"
    text = (row["text"] or "").strip()
    return "emoji" if text and _EMOJI_ONLY.fullmatch(text) else "text"


def service_action(row: Any) -> str | None:
    """Readable service action: MessageActionPinMessage → pin_message."""
    action = (row["service_action"] or "").removeprefix("MessageAction")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", action).lower() or None


def message_payload(
    row: Any,
    files: list[Any],
    node: Node,
    zone: tzinfo,
    *,
    include_chat: bool = False,
    meta: bool = False,
) -> dict[str, Any]:
    """``id``, ``time``, ``sender``, ``type``, ``text`` by default; ``meta`` adds every stored
    field. Times are printed as "yyyy-mm-dd HH:MM:SS" in ``zone``.

    The short form keeps ``path`` for media already on this machine, so downloads stay usable,
    or ``download_status`` when a file was skipped, failed or went missing.
    """
    payload: dict[str, Any] = {"id": row["id"], "time": display_time(row["date"], zone)}
    if include_chat:
        payload["chat"] = compact(
            {"id": row["chat_id"], "name": row["chat_name"] or row["chat_username"]}
        )
    kind = message_type(row)
    if not meta:
        sender = _sender_label(row)
        if sender is not None:
            payload["sender"] = sender
        payload["type"] = kind
        if kind == "service":
            payload["action"] = service_action(row)
        payload["text"] = row["text"] or ""
        if kind == "sticker" and row["meta"]:
            emoji = json.loads(row["meta"]).get("sticker_emoji")
            if emoji:
                payload["emoji"] = emoji
        if row["media_type"]:
            media = media_payload(row, files, node)
            if "local_path" in media:
                payload["path"] = media["local_path"]
            elif media.get("download_status") in MISSING_FILE_STATUSES:
                payload["download_status"] = media["download_status"]
                if "download_error" in media:
                    payload["download_error"] = media["download_error"]
        return payload

    sender = compact(
        {"id": row["sender_id"], "username": row["sender_username"], "name": row["sender_name"]}
    )
    if sender:
        payload["sender"] = sender
    payload["type"] = kind
    payload["text"] = row["text"] or ""
    payload["outgoing"] = bool(row["outgoing"])
    payload.update(
        compact(
            {
                "edit_date": display_time(row["edit_date"], zone),
                "reply_to_message_id": row["reply_to_message_id"],
                "grouped_id": row["grouped_id"],
                "action": service_action(row),
            }
        )
    )
    if row["meta"]:
        extra = json.loads(row["meta"])
        if extra.get("forward", {}).get("date"):
            extra["forward"]["date"] = display_time(extra["forward"]["date"], zone)
        payload.update(extra)
    if row["media_type"]:
        payload["media"] = media_payload(row, files, node)
    return payload


def messages_payload(
    store: Any, rows: list[Any], *, include_chat: bool = False, meta: bool = False
) -> list[Any]:
    """Payloads for many rows, fetching their stored files in one query."""
    files = store.files_for((row["chat_id"], row["id"]) for row in rows if row["media_type"])
    zone = display_timezone()
    return [
        message_payload(
            row,
            files.get((row["chat_id"], row["id"]), []),
            store.node,
            zone,
            include_chat=include_chat,
            meta=meta,
        )
        for row in rows
    ]
