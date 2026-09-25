"""`tele send` and `tele wait`; every message they see is saved to the local store."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from telethon import events

from ..config import Credentials
from ..errors import TeleError
from ..store import Store
from ..store.payloads import messages_payload, peer_payload
from .client import authorized_client, resolve_chat
from .records import message_record


def _stored_payload(store: Store, chat_id: int, message: Any) -> dict[str, Any]:
    store.save_messages(chat_id, [message_record(message)])
    return messages_payload(store, [store.message(chat_id, message.id)])[0]


async def send_text(
    credentials: Credentials,
    session: Path,
    store: Store,
    target_value: str,
    text: str,
    reply_to: int | None,
    link_preview: bool,
    dry_run: bool,
) -> dict[str, Any]:
    async with authorized_client(credentials, session) as client:
        input_entity, chat = await resolve_chat(client, store, target_value)
        request = {
            "target": target_value,
            "reply_to_message_id": reply_to,
            "link_preview": link_preview,
            "text": text,
            "text_length": len(text),
        }
        if dry_run:
            return {"ok": True, "action": "dry_run", "chat": peer_payload(chat), "request": request}

        try:
            message = await client.send_message(
                input_entity,
                text,
                reply_to=reply_to,
                parse_mode=None,
                link_preview=link_preview,
            )
        except (TimeoutError, OSError) as exc:
            raise TeleError(
                "delivery_unknown",
                "The connection failed while sending; Telegram delivery status is unknown.",
                exit_code=7,
                hint=(
                    "Do not automatically retry. Check the conversation first to avoid "
                    "sending a duplicate."
                ),
            ) from exc

        return {
            "ok": True,
            "action": "sent",
            "chat": peer_payload(chat),
            "message": _stored_payload(store, chat["id"], message),
        }


async def wait_for_message(
    credentials: Credentials,
    session: Path,
    store: Store,
    target_value: str,
    after_message_id: int | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Wait for the first incoming message newer than a per-chat cursor."""
    async with authorized_client(credentials, session, auto_reconnect=True) as client:
        input_entity, chat = await resolve_chat(client, store, target_value)
        chat_payload = peer_payload(chat)

        if after_message_id is None:
            latest = await client.get_messages(input_entity, limit=1)
            baseline = latest[0].id if latest else 0
        else:
            baseline = after_message_id

        def timeout() -> dict[str, Any]:
            return {
                "ok": True,
                "event": "timeout",
                "chat": chat_payload,
                "cursor": {"after_message_id": baseline},
                "timeout_seconds": timeout_seconds,
            }

        async def arrived(message: Any) -> dict[str, Any]:
            await message.get_sender()
            return {
                "ok": True,
                "event": "message",
                "chat": chat_payload,
                "cursor": {"after_message_id": baseline},
                "message": _stored_payload(store, chat["id"], message),
            }

        queue: asyncio.Queue[Any] = asyncio.Queue()

        async def on_new_message(event: Any) -> None:
            await queue.put(event.message)

        event_builder = events.NewMessage(chats=input_entity, incoming=True)
        client.add_event_handler(on_new_message, event_builder)
        try:
            # Close the gap between selecting the cursor and registering the handler.
            # The live handler covers anything arriving while this history scan runs.
            async for message in client.iter_messages(
                input_entity,
                min_id=baseline,
                reverse=True,
            ):
                if message.id > baseline and not getattr(message, "out", False):
                    return await arrived(message)

            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_seconds
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return timeout()
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=remaining)
                except TimeoutError:
                    return timeout()
                if message.id <= baseline or getattr(message, "out", False):
                    continue
                return await arrived(message)
        finally:
            client.remove_event_handler(on_new_message, event_builder)
