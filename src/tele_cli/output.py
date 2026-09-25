"""JSON and text rendering shared by `tele` and `tele-local`."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO


def _is_record_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) for item in value)


def dump_json(payload: dict[str, Any], stream: TextIO | None = None) -> None:
    """Pretty-print, but keep each record of a list (messages, chats, rows) on one line."""
    stream = stream or sys.stdout
    entries = []
    for key, value in payload.items():
        if _is_record_list(value):
            records = ",\n".join(f"    {json.dumps(item, ensure_ascii=False)}" for item in value)
            rendered = f"[\n{records}\n  ]"
        else:
            rendered = json.dumps(value, ensure_ascii=False, indent=2).replace("\n", "\n  ")
        entries.append(f"  {json.dumps(key)}: {rendered}")
    stream.write("{\n" + ",\n".join(entries) + "\n}\n")


def dump_json_line(payload: dict[str, Any], stream: TextIO | None = None) -> None:
    """Write one flush-safe JSON line for long-running commands."""
    stream = stream or sys.stdout
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stream.flush()


def _label(peer: dict[str, Any] | str | int | None) -> str:
    """Display name of a peer record, or the short ``sender`` label as-is."""
    if not isinstance(peer, dict):
        return str(peer or "unknown")
    return str(peer.get("name") or peer.get("username") or peer.get("id") or "unknown")


def render_messages(payload: dict[str, Any]) -> str:
    lines = []
    if payload.get("chat"):
        chat = payload["chat"]
        lines.append(f"Chat: {_label(chat)} ({chat['id']})")
    for message in payload["messages"]:
        where = f" in {_label(message['chat'])}" if message.get("chat") else ""
        sender = f" {_label(message['sender'])}" if message.get("sender") else ""
        details = message.get("media") or {}
        kind = message.get("type", "text")
        if kind == "service":
            label = f"[{message.get('action') or kind}]"
        else:
            label = f"[{kind}]" if kind not in ("text", "emoji") else ""
        lines += [
            "",
            f"[{message['time']}] #{message['id']}{sender}{where}",
            " ".join(filter(None, [label, message.get("text") or message.get("emoji")]))
            or "[empty]",
        ]
        path = message.get("path") or details.get("local_path")
        status = details.get("download_status") or message.get("download_status")
        if path:
            lines.append(f"Media: {path}")
        elif details.get("files"):
            lines += [f"Media on {f['user']}@{f['host']}: {f['path']}" for f in details["files"]]
        elif status:
            lines.append(f"Media: {status}")
    return "\n".join(lines)


def render_table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    """Tab-separated rows with a header line; empty cells shown as "-"."""
    if not rows:
        return "(no rows)"
    columns = columns or list(dict.fromkeys(key for row in rows for key in row))

    def cell(value: Any) -> str:
        if value is None or value == "":
            return "-"
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return text.replace("\t", " ").replace("\n", " ")

    return "\n".join(
        ["\t".join(columns), *("\t".join(cell(row.get(col)) for col in columns) for row in rows)]
    )
