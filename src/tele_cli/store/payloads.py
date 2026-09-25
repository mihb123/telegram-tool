"""Convert store rows into the compact JSON shapes printed by `tele` and `tele-local`.

Empty values are left out so long conversations stay short for agents to read.
"""

from __future__ import annotations

import os
from typing import Any

from ..config import Node


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
            "type": row["media_type"],
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


def message_payload(
    row: Any, files: list[Any], node: Node, *, include_chat: bool = False
) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": row["id"], "date": row["date"]}
    if include_chat:
        payload["chat"] = compact(
            {"id": row["chat_id"], "name": row["chat_name"] or row["chat_username"]}
        )
    sender = compact(
        {"id": row["sender_id"], "username": row["sender_username"], "name": row["sender_name"]}
    )
    if sender:
        payload["sender"] = sender
    payload["outgoing"] = bool(row["outgoing"])
    payload.update(
        compact(
            {
                "text": row["text"],
                "edit_date": row["edit_date"],
                "reply_to_message_id": row["reply_to_message_id"],
                "grouped_id": row["grouped_id"],
                "service_action": row["service_action"],
            }
        )
    )
    if row["media_type"]:
        payload["media"] = media_payload(row, files, node)
    return payload


def messages_payload(store: Any, rows: list[Any], *, include_chat: bool = False) -> list[Any]:
    """Payloads for many rows, fetching their stored files in one query."""
    files = store.files_for((row["chat_id"], row["id"]) for row in rows if row["media_type"])
    return [
        message_payload(
            row, files.get((row["chat_id"], row["id"]), []), store.node, include_chat=include_chat
        )
        for row in rows
    ]
