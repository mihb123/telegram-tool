"""Telegram connection, authorization and chat resolution."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError

from ..config import Credentials, secure_data_directory, secure_session_file
from ..errors import TeleError
from ..store import Store
from ..values import parse_target
from .records import peer_record


def _flood_wait_error(exc: FloodWaitError) -> TeleError:
    return TeleError(
        "flood_wait",
        f"Telegram requires waiting {exc.seconds} seconds before retrying.",
        exit_code=75,
        details={"retry_after_seconds": exc.seconds},
    )


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
        raise _flood_wait_error(exc) from exc
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


async def resolve_chat(
    client: TelegramClient, store: Store, target_value: str
) -> tuple[Any, dict[str, Any]]:
    """Return the input entity for API calls and the chat's peer record (saved locally).

    The first connection from a machine also records which account it is logged in to.
    """
    target = parse_target(target_value)
    if store.account_id is None:
        store.register_node(peer_record(await client.get_me()))
    else:
        store.touch_node()
    try:
        input_entity = await client.get_input_entity(target)
        entity = await client.get_entity(input_entity)
    except (ValueError, TypeError) as exc:
        raise TeleError(
            "peer_not_found",
            f"Cannot resolve Telegram chat {target_value!r}.",
            exit_code=5,
            hint="Check the username/ID. For private groups, run `tele dialogs` to find its ID.",
        ) from exc
    chat = peer_record(entity)
    store.save_peers([chat])
    return input_entity, chat


async def authorize(
    credentials: Credentials, session: Path, store: Store, phone: str | None
) -> dict[str, Any]:
    client = _client(credentials, session)
    try:
        await client.start(phone=phone or (lambda: input("Phone number: ").strip()))
        account = peer_record(await client.get_me())
        store.register_node(account)
        return {"ok": True, "authorized": True, "account": account, "node": str(store.node)}
    except FloodWaitError as exc:
        raise _flood_wait_error(exc) from exc
    except RPCError as exc:
        raise TeleError("telegram_error", f"Telegram authorization failed: {exc}") from exc
    finally:
        if client.is_connected():
            await client.disconnect()
        secure_session_file()


async def status(credentials: Credentials, session: Path, store: Store) -> dict[str, Any]:
    if not (session if session.suffix == ".session" else session.with_suffix(".session")).exists():
        return {"ok": True, "authorized": False, "session_exists": False}
    async with authorized_client(credentials, session) as client:
        account = peer_record(await client.get_me())
    store.register_node(account)
    return {
        "ok": True,
        "authorized": True,
        "session_exists": True,
        "account": account,
        "node": str(store.node),
        "database": store.db.label,
    }
