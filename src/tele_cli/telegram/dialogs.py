"""`tele dialogs`: list recent chats and remember them for local target resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Credentials, display_timezone
from ..store import Store
from ..store.payloads import compact
from ..values import display_time, to_iso
from .client import authorized_client
from .records import peer_record


async def fetch_dialogs(
    credentials: Credentials, session: Path, store: Store, limit: int
) -> dict[str, Any]:
    async with authorized_client(credentials, session) as client:
        dialogs = [dialog async for dialog in client.iter_dialogs(limit=limit)]

    rows = [
        (
            peer_record(dialog.entity),
            dialog.unread_count,
            to_iso(getattr(getattr(dialog, "message", None), "date", None)),
        )
        for dialog in dialogs
    ]
    store.save_dialogs(rows)
    zone = display_timezone()
    items = [
        compact(
            {
                **peer,
                "id": dialog.id,
                "name": dialog.name,
                "unread_count": unread,
                "last_message_date": display_time(last, zone),
            }
        )
        for dialog, (peer, unread, last) in zip(dialogs, rows, strict=True)
    ]
    return {"ok": True, "count": len(items), "dialogs": items}
