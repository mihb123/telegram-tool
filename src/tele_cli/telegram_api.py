from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events, utils
from telethon.errors import FloodWaitError, RPCError

from .config import Credentials, default_media_root, secure_data_directory, secure_session_file
from .errors import TeleError

DEFAULT_MAX_MEDIA_BYTES = 100 * 1024 * 1024


class _MediaSizeLimitExceeded(Exception):
    pass


def parse_target(value: str) -> str | int:
    target = value.strip()
    if not target:
        raise TeleError("invalid_target", "Chat target cannot be empty.", exit_code=2)
    if target.lstrip("-").isdigit():
        return int(target)
    if target.lower() == "me":
        return "me"
    return target[1:] if target.startswith("@") else target


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _media_type(message: Any) -> str | None:
    media = getattr(message, "media", None)
    if media is None:
        return None
    name = type(media).__name__
    return name.removeprefix("MessageMedia") or name


def _media_info(message: Any) -> dict[str, Any] | None:
    if getattr(message, "media", None) is None:
        return None
    file = getattr(message, "file", None)
    return {
        "type": _media_type(message),
        "name": getattr(file, "name", None),
        "extension": getattr(file, "ext", None),
        "mime_type": getattr(file, "mime_type", None),
        "size": getattr(file, "size", None),
        "width": getattr(file, "width", None),
        "height": getattr(file, "height", None),
        "duration": getattr(file, "duration", None),
        "local_path": None,
        "download_status": None,
    }


def serialize_message(message: Any) -> dict[str, Any]:
    sender = getattr(message, "sender", None)
    sender_name = utils.get_display_name(sender) if sender is not None else None
    action = getattr(message, "action", None)
    return {
        "id": message.id,
        "date": _iso(getattr(message, "date", None)),
        "edit_date": _iso(getattr(message, "edit_date", None)),
        "chat_id": getattr(message, "chat_id", None),
        "sender": {
            "id": getattr(message, "sender_id", None),
            "username": getattr(sender, "username", None),
            "name": sender_name or None,
        },
        "outgoing": bool(getattr(message, "out", False)),
        "text": getattr(message, "raw_text", None) or "",
        "reply_to_message_id": getattr(message, "reply_to_msg_id", None),
        "has_media": getattr(message, "media", None) is not None,
        "media_type": _media_type(message),
        "media": _media_info(message),
        "service_action": type(action).__name__ if action is not None else None,
    }


def _safe_component(value: str, fallback: str) -> str:
    component = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return component[:100] or fallback


def _default_media_directory(target_value: str) -> Path:
    target = _safe_component(target_value.removeprefix("@"), "chat")
    return default_media_root() / target


def _media_destination(message: Any, media: dict[str, Any], directory: Path) -> Path:
    raw_name = media.get("name")
    raw_extension = str(media.get("extension") or "").lstrip(".")
    safe_extension = re.sub(r"[^A-Za-z0-9.]+", "", raw_extension)[:20]
    extension = f".{safe_extension}" if safe_extension else ""
    if raw_name:
        name = _safe_component(Path(str(raw_name)).name, "media")
        if extension and not name.lower().endswith(str(extension).lower()):
            name += str(extension)
    else:
        media_type = str(media.get("type") or "media").lower()
        name = f"{_safe_component(media_type, 'media')}{extension}"
    destination = directory / f"{message.id}_{name}"
    expected_size = media.get("size")
    matches_existing = destination.exists() and (
        expected_size is None or destination.stat().st_size == expected_size
    )
    if matches_existing:
        return destination
    if not destination.exists():
        return destination
    for suffix in range(1, 10_000):
        candidate = destination.with_name(f"{destination.stem}_{suffix}{destination.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"Cannot allocate a unique filename for {destination.name}")


async def _download_message_media(
    client: TelegramClient,
    message: Any,
    directory: Path,
    max_media_bytes: int,
) -> dict[str, Any] | None:
    media = _media_info(message)
    if media is None:
        return None

    size = media.get("size")
    if max_media_bytes and size is not None and size > max_media_bytes:
        media["download_status"] = "skipped_too_large"
        media["max_size_bytes"] = max_media_bytes
        return media

    destination = _media_destination(message, media, directory)
    if destination.exists():
        media["local_path"] = str(destination.resolve())
        media["download_status"] = "existing"
        return media

    def enforce_size_limit(received: int, total: int) -> None:
        known_total = total or 0
        if max_media_bytes and (received > max_media_bytes or known_total > max_media_bytes):
            raise _MediaSizeLimitExceeded

    try:
        downloaded = await client.download_media(
            message,
            file=str(destination),
            progress_callback=enforce_size_limit,
        )
    except _MediaSizeLimitExceeded:
        destination.unlink(missing_ok=True)
        media["download_status"] = "skipped_too_large"
        media["max_size_bytes"] = max_media_bytes
        return media
    except FloodWaitError:
        destination.unlink(missing_ok=True)
        raise
    except (OSError, RPCError, ValueError) as exc:
        destination.unlink(missing_ok=True)
        media["download_status"] = "error"
        media["download_error"] = str(exc)
        return media

    if downloaded is None:
        destination.unlink(missing_ok=True)
        media["download_status"] = "not_downloadable"
        return media

    downloaded_path = Path(str(downloaded)).expanduser().resolve()
    if not downloaded_path.is_file():
        media["download_status"] = "error"
        media["download_error"] = f"Telegram reported a missing download path: {downloaded_path}"
        return media
    downloaded_path.chmod(0o600)
    media["local_path"] = str(downloaded_path)
    media["download_status"] = "downloaded"
    return media


def _entity_info(entity: Any) -> dict[str, Any]:
    return {
        "id": utils.get_peer_id(entity),
        "type": type(entity).__name__,
        "username": getattr(entity, "username", None),
        "name": utils.get_display_name(entity) or None,
    }


def _client(
    credentials: Credentials,
    session: Path,
    *,
    auto_reconnect: bool = False,
) -> TelegramClient:
    secure_data_directory()
    return TelegramClient(
        str(session),
        credentials.api_id,
        credentials.api_hash,
        connection_retries=2,
        request_retries=2,
        retry_delay=1,
        flood_sleep_threshold=0,
        auto_reconnect=auto_reconnect,
        timeout=10,
    )


@asynccontextmanager
async def authorized_client(
    credentials: Credentials,
    session: Path,
    *,
    auto_reconnect: bool = False,
) -> AsyncIterator[TelegramClient]:
    client = _client(credentials, session, auto_reconnect=auto_reconnect)
    try:
        await asyncio.wait_for(client.connect(), timeout=20)
        if not await client.is_user_authorized():
            raise TeleError(
                "not_authorized",
                "Telegram session is not authorized.",
                exit_code=4,
                hint="Run `tele auth` interactively, then retry.",
            )
        yield client
    except TeleError:
        raise
    except FloodWaitError as exc:
        raise TeleError(
            "flood_wait",
            f"Telegram requires waiting {exc.seconds} seconds before retrying.",
            exit_code=75,
            details={"retry_after_seconds": exc.seconds},
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise TeleError(
            "connection_error",
            f"Cannot connect to Telegram: {exc}",
            hint="Check the network connection and retry once.",
        ) from exc
    except RPCError as exc:
        raise TeleError("telegram_error", f"Telegram API error: {exc}") from exc
    finally:
        if client.is_connected():
            await client.disconnect()
        secure_session_file()


async def authorize(credentials: Credentials, session: Path, phone: str | None) -> dict[str, Any]:
    client = _client(credentials, session)
    try:
        await client.start(phone=phone or (lambda: input("Phone number: ").strip()))
        me = await client.get_me()
        return {"ok": True, "authorized": True, "account": _entity_info(me)}
    except FloodWaitError as exc:
        raise TeleError(
            "flood_wait",
            f"Telegram requires waiting {exc.seconds} seconds before retrying.",
            exit_code=75,
            details={"retry_after_seconds": exc.seconds},
        ) from exc
    except RPCError as exc:
        raise TeleError("telegram_error", f"Telegram authorization failed: {exc}") from exc
    finally:
        if client.is_connected():
            await client.disconnect()
        secure_session_file()


async def status(credentials: Credentials, session: Path) -> dict[str, Any]:
    if not (session if session.suffix == ".session" else session.with_suffix(".session")).exists():
        return {"ok": True, "authorized": False, "session_exists": False}
    async with authorized_client(credentials, session) as client:
        me = await client.get_me()
        return {
            "ok": True,
            "authorized": True,
            "session_exists": True,
            "account": _entity_info(me),
        }


async def fetch_messages(
    credentials: Credentials,
    session: Path,
    target_value: str,
    limit: int,
    *,
    download_media: bool = False,
    download_dir: Path | None = None,
    max_media_bytes: int = DEFAULT_MAX_MEDIA_BYTES,
) -> dict[str, Any]:
    target = parse_target(target_value)
    async with authorized_client(credentials, session) as client:
        try:
            input_entity = await client.get_input_entity(target)
            entity = await client.get_entity(input_entity)
        except (ValueError, TypeError) as exc:
            raise TeleError(
                "peer_not_found",
                f"Cannot resolve Telegram chat {target_value!r}.",
                exit_code=5,
                hint=(
                    "Check the username/ID. For private groups, run `tele dialogs` to find its ID."
                ),
            ) from exc

        media_directory: Path | None = None
        if download_media:
            media_directory = (download_dir or _default_media_directory(target_value)).resolve()
            media_directory_existed = media_directory.exists()
            try:
                media_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                if not media_directory_existed:
                    media_directory.chmod(0o700)
            except OSError as exc:
                raise TeleError(
                    "media_directory_error",
                    f"Cannot create media directory {media_directory}: {exc}",
                    exit_code=8,
                ) from exc

        messages: list[dict[str, Any]] = []
        async for message in client.iter_messages(input_entity, limit=limit):
            serialized = serialize_message(message)
            if media_directory is not None and serialized["has_media"]:
                serialized["media"] = await _download_message_media(
                    client, message, media_directory, max_media_bytes
                )
            messages.append(serialized)

        payload = {
            "ok": True,
            "query": {
                "target": target_value,
                "limit": limit,
                "order": "newest_first",
                "download_media": download_media,
                "max_media_bytes": max_media_bytes if download_media else None,
            },
            "chat": _entity_info(entity),
            "count": len(messages),
            "messages": messages,
        }
        if media_directory is not None:
            statuses = [
                message["media"]["download_status"]
                for message in messages
                if message.get("media") and message["media"].get("download_status")
            ]
            payload["media_directory"] = str(media_directory)
            payload["media_summary"] = {
                "downloaded": statuses.count("downloaded"),
                "existing": statuses.count("existing"),
                "skipped_too_large": statuses.count("skipped_too_large"),
                "not_downloadable": statuses.count("not_downloadable"),
                "errors": statuses.count("error"),
            }
        return payload


async def send_text(
    credentials: Credentials,
    session: Path,
    target_value: str,
    text: str,
    reply_to: int | None,
    link_preview: bool,
    dry_run: bool,
) -> dict[str, Any]:
    target = parse_target(target_value)
    async with authorized_client(credentials, session) as client:
        try:
            input_entity = await client.get_input_entity(target)
            entity = await client.get_entity(input_entity)
        except (ValueError, TypeError) as exc:
            raise TeleError(
                "peer_not_found",
                f"Cannot resolve Telegram chat {target_value!r}.",
                exit_code=5,
                hint=(
                    "Check the username/ID. For private groups, run `tele dialogs` to find its ID."
                ),
            ) from exc

        chat = _entity_info(entity)
        request = {
            "target": target_value,
            "reply_to_message_id": reply_to,
            "link_preview": link_preview,
            "text": text,
            "text_length": len(text),
        }
        if dry_run:
            return {"ok": True, "action": "dry_run", "chat": chat, "request": request}

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
            "chat": chat,
            "message": serialize_message(message),
        }


async def _serialize_with_sender(message: Any) -> dict[str, Any]:
    get_sender = getattr(message, "get_sender", None)
    if callable(get_sender):
        await get_sender()
    return serialize_message(message)


async def wait_for_message(
    credentials: Credentials,
    session: Path,
    target_value: str,
    after_message_id: int | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Wait for the first incoming message newer than a per-chat cursor."""
    target = parse_target(target_value)
    async with authorized_client(credentials, session, auto_reconnect=True) as client:
        try:
            input_entity = await client.get_input_entity(target)
            entity = await client.get_entity(input_entity)
        except (ValueError, TypeError) as exc:
            raise TeleError(
                "peer_not_found",
                f"Cannot resolve Telegram chat {target_value!r}.",
                exit_code=5,
                hint=(
                    "Check the username/ID. For private groups, run `tele dialogs` to find its ID."
                ),
            ) from exc

        if after_message_id is None:
            latest = await client.get_messages(input_entity, limit=1)
            baseline = latest[0].id if latest else 0
        else:
            baseline = after_message_id

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
                    return {
                        "ok": True,
                        "event": "message",
                        "chat": _entity_info(entity),
                        "cursor": {"after_message_id": baseline},
                        "message": await _serialize_with_sender(message),
                    }

            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_seconds
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return {
                        "ok": True,
                        "event": "timeout",
                        "chat": _entity_info(entity),
                        "cursor": {"after_message_id": baseline},
                        "timeout_seconds": timeout_seconds,
                    }
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=remaining)
                except TimeoutError:
                    return {
                        "ok": True,
                        "event": "timeout",
                        "chat": _entity_info(entity),
                        "cursor": {"after_message_id": baseline},
                        "timeout_seconds": timeout_seconds,
                    }
                if message.id <= baseline or getattr(message, "out", False):
                    continue
                return {
                    "ok": True,
                    "event": "message",
                    "chat": _entity_info(entity),
                    "cursor": {"after_message_id": baseline},
                    "message": await _serialize_with_sender(message),
                }
        finally:
            client.remove_event_handler(on_new_message, event_builder)


async def fetch_dialogs(credentials: Credentials, session: Path, limit: int) -> dict[str, Any]:
    async with authorized_client(credentials, session) as client:
        dialogs: list[dict[str, Any]] = []
        async for dialog in client.iter_dialogs(limit=limit):
            entity = dialog.entity
            dialogs.append(
                {
                    "id": dialog.id,
                    "name": dialog.name,
                    "type": type(entity).__name__,
                    "username": getattr(entity, "username", None),
                    "unread_count": dialog.unread_count,
                    "last_message_date": _iso(
                        getattr(getattr(dialog, "message", None), "date", None)
                    ),
                }
            )
        return {"ok": True, "count": len(dialogs), "dialogs": dialogs}
