"""Persistent Telegram listener that writes every incoming message to the durable inbox."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import stat
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from typing import Any

from telethon import events
from telethon.errors import RPCError

from ..config import Credentials, listener_socket_path, secure_data_directory
from ..errors import TeleError, delivery_unknown
from ..store import Store
from ..store.payloads import messages_payload, peer_payload
from .client import authorized_client, resolve_chat
from .messaging import send_text_with_client
from .records import message_record, peer_record

Emit = Callable[[dict[str, Any]], None]
HEARTBEAT_SECONDS = 10
IPC_TIMEOUT_SECONDS = 30
CAPTURE_ATTEMPTS = 3
CAPTURE_BACKOFF_SECONDS = 1


def _listener_error(exc: BaseException) -> TeleError:
    if isinstance(exc, TeleError):
        return exc
    return TeleError(
        "listener_error",
        f"The Telegram listener stopped after an unexpected error: {exc}",
        exit_code=10,
        hint="The service supervisor may restart it; inspect the listener logs.",
    )


def _raise_response_error(response: dict[str, Any]) -> None:
    error = response.get("error") or {}
    known = {"code", "message", "hint", "exit_code"}
    raise TeleError(
        str(error.get("code") or "listener_error"),
        str(error.get("message") or "The listener rejected the request."),
        exit_code=int(error.get("exit_code") or 1),
        hint=error.get("hint"),
        details={key: value for key, value in error.items() if key not in known},
    )


def _is_infrastructure_error(exc: Exception, store: Store) -> bool:
    """Identify failures that make continued durable listening unsafe."""
    if isinstance(exc, TeleError):
        return exc.code in {"store_error", "connection_error"}
    return isinstance(exc, (OSError, TimeoutError, sqlite3.Error, *store.db.errors))


def _ipc_error(exc: TeleError) -> dict[str, Any]:
    """Preserve the CLI exit code across the listener's JSON socket protocol."""
    response = exc.as_dict()
    response["error"]["exit_code"] = exc.exit_code
    return response


def _message_error(event: Any, exc: BaseException) -> dict[str, Any]:
    return {
        "event": "message_error",
        "chat_id": getattr(event, "chat_id", None),
        "message_id": getattr(event.message, "id", None),
        **_listener_error(exc).as_dict(),
    }


async def send_via_listener(request: dict[str, Any]) -> dict[str, Any] | None:
    """Send an IPC request when the local listener socket exists; otherwise return None."""
    path = listener_socket_path()
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise TeleError(
            "listener_unavailable", f"Cannot inspect listener socket {path}: {exc}"
        ) from exc
    if not stat.S_ISSOCK(mode):
        raise TeleError(
            "listener_socket_error",
            f"Listener socket path exists but is not a socket: {path}",
            hint="Move that file or set TELE_LISTENER_SOCKET to a safe unused path.",
        )

    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), timeout=3)
    except (OSError, TimeoutError):
        return None

    try:
        try:
            writer.write((json.dumps(request, ensure_ascii=False) + "\n").encode())
        except OSError as exc:
            raise TeleError(
                "listener_unavailable",
                f"Cannot send through the active listener at {path}: {exc}",
                hint="Check `tele-local listeners` and the listener service logs.",
            ) from exc
        # From here on the listener may already be sending the message.
        try:
            await writer.drain()
            raw = await asyncio.wait_for(reader.readline(), timeout=IPC_TIMEOUT_SECONDS)
        except (OSError, TimeoutError) as exc:
            raise delivery_unknown(
                "The listener connection failed after accepting the send request; "
                "Telegram delivery status is unknown."
            ) from exc
    finally:
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()

    if not raw:
        raise delivery_unknown(
            "The listener closed without confirming whether Telegram accepted the message."
        )
    try:
        response = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        response = None
    if not isinstance(response, dict):
        raise delivery_unknown(
            "The listener returned an invalid acknowledgement; delivery status is unknown."
        )
    if not response.get("ok"):
        _raise_response_error(response)
    return response


async def _serve_ipc(handle: Callable[..., Any], socket_path: Path) -> asyncio.Server:
    """Bind the private socket `tele send` uses, replacing a stale socket from a crash."""
    secure_data_directory()
    if socket_path.exists():
        if not stat.S_ISSOCK(socket_path.stat().st_mode):
            raise TeleError(
                "listener_socket_error",
                f"Listener socket path exists but is not a socket: {socket_path}",
            )
        socket_path.unlink()
    server = await asyncio.start_unix_server(handle, path=str(socket_path))
    socket_path.chmod(0o600)
    return server


async def listen_forever(
    credentials: Credentials,
    session: Path,
    store: Store,
    emit: Emit,
    *,
    print_events: bool,
    targets: tuple[str, ...],
) -> dict[str, Any]:
    """Keep one Telegram connection alive and enqueue incoming messages from allowed chats."""
    process_id = os.getpid()
    store.assert_listener_available()
    async with authorized_client(
        credentials, session, receive_updates=True, own_session=True
    ) as client:
        account = peer_record(await client.get_me())
        store.register_node(account)
        input_entities: list[Any] = []
        allowed_chats: dict[int, dict[str, Any]] = {}
        for target in targets:
            input_entity, chat = await resolve_chat(client, store, target)
            if chat["id"] not in allowed_chats:
                input_entities.append(input_entity)
                allowed_chats[chat["id"]] = chat
        store.claim_listener(process_id)

        # Inbox writes and heartbeats get their own thread and database connection, so a slow
        # database never stalls the event loop that keeps the Telegram connection alive.
        loop = asyncio.get_running_loop()
        db_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tele-inbox")
        inbox_store: Store | None = None
        send_lock = asyncio.Lock()
        fatal: asyncio.Future[BaseException] = loop.create_future()

        async def in_db(function: Callable[..., Any], *args: Any) -> Any:
            return await loop.run_in_executor(db_thread, function, *args)

        async def chat_of(event: Any) -> dict[str, Any]:
            """Fresh chat details when Telegram provides them, else the allowlist record."""
            try:
                entity = await event.get_chat()
            except (RPCError, ValueError):
                entity = None
            return peer_record(entity) if entity is not None else allowed_chats[event.chat_id]

        async def capture(event: Any) -> tuple[dict[str, Any], int | None]:
            chat = await chat_of(event)
            with suppress(RPCError, ValueError):
                await event.get_sender()  # optional profile; sender_id is stored regardless
            record = message_record(event.message)
            event_id = await in_db(inbox_store.save_inbox_message, chat, record)
            client.session.save()
            return chat, event_id

        def printed_message(chat_id: int, message_id: int) -> dict[str, Any]:
            row = inbox_store.message(chat_id, message_id)
            if row is None:
                raise RuntimeError(f"Stored message {message_id} cannot be read back")
            return messages_payload(inbox_store, [row], meta=True)[0]

        async def on_new_message(event: Any) -> None:
            # Nothing re-delivers a message missed here, so retry transient failures first.
            for attempt in range(1, CAPTURE_ATTEMPTS + 1):
                try:
                    chat, event_id = await capture(event)
                    break
                except Exception as exc:
                    if attempt < CAPTURE_ATTEMPTS:
                        await asyncio.sleep(attempt * CAPTURE_BACKOFF_SECONDS)
                        continue
                    emit(_message_error(event, exc))
                    if _is_infrastructure_error(exc, store) and not fatal.done():
                        fatal.set_result(exc)
                    return
            if event_id is None or not print_events:
                return
            try:
                payload = await in_db(printed_message, chat["id"], event.message.id)
            except Exception as exc:  # already stored: only the printout is lost
                emit(_message_error(event, exc))
                return
            emit(
                {
                    "ok": True,
                    "event": "message",
                    "cursor": {"after_event_id": event_id},
                    "chat": peer_payload(chat),
                    "message": payload,
                }
            )

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                client.session.save()
                await in_db(inbox_store.heartbeat_listener, process_id)

        async def handle_ipc(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                raw = await asyncio.wait_for(reader.readline(), timeout=5)
                request = json.loads(raw)
                if not isinstance(request, dict) or request.get("action") != "send":
                    raise TeleError("invalid_listener_request", "Unsupported listener request.")
                async with send_lock:
                    response = await send_text_with_client(
                        client,
                        store,
                        str(request.get("target") or ""),
                        str(request.get("text") or ""),
                        request.get("reply_to"),
                        bool(request.get("link_preview")),
                        bool(request.get("dry_run")),
                    )
            except TeleError as exc:
                response = _ipc_error(exc)
            except (json.JSONDecodeError, UnicodeDecodeError, TimeoutError) as exc:
                response = _ipc_error(
                    TeleError("invalid_listener_request", f"Cannot decode listener request: {exc}")
                )
            except BaseException as exc:
                response = _ipc_error(_listener_error(exc))
            writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
            try:
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        event_builder = events.NewMessage(chats=input_entities, incoming=True)
        socket_path = listener_socket_path()
        ipc_server: asyncio.Server | None = None
        heartbeat_task: asyncio.Task[None] | None = None
        connection_task: asyncio.Task[Any] | None = None
        try:
            inbox_store = await in_db(Store.open)
            client.add_event_handler(on_new_message, event_builder)
            ipc_server = await _serve_ipc(handle_ipc, socket_path)
            await client.catch_up()
            client.session.save()
            emit(
                {
                    "ok": True,
                    "event": "ready",
                    "account": account,
                    "cursor": {"after_event_id": store.latest_inbox_event_id()},
                    "process_id": process_id,
                    "socket": str(socket_path),
                    "listen_chat_ids": list(allowed_chats),
                }
            )
            heartbeat_task = asyncio.create_task(heartbeat())
            connection_task = asyncio.create_task(client.run_until_disconnected())
            done, _ = await asyncio.wait(
                (connection_task, heartbeat_task, fatal),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if fatal in done:
                raise _listener_error(fatal.result())
            if heartbeat_task in done:
                heartbeat_task.result()
            if connection_task in done:
                connection_task.result()
            return {
                "ok": True,
                "event": "stopped",
                "cursor": {"after_event_id": store.latest_inbox_event_id()},
            }
        finally:
            client.remove_event_handler(on_new_message, event_builder)
            if ipc_server is not None:
                ipc_server.close()
                await ipc_server.wait_closed()
                socket_path.unlink(missing_ok=True)
            tasks = [task for task in (heartbeat_task, connection_task) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            store.release_listener(process_id)
            if inbox_store is not None:
                with suppress(Exception):
                    await in_db(inbox_store.close)
            db_thread.shutdown(wait=False)
