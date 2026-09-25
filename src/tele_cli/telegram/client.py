"""Telegram connection, authorization and chat resolution."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing, suppress
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import SQLiteSession

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


class _SessionSnapshot(SQLiteSession):
    """In-memory copy of the session file for commands other than `tele listen` and `tele auth`.

    The listener keeps the file open for its whole life: a copy never waits on or holds the
    file's write lock and never overwrites the listener's update state. Entities learned here
    are merged back on close so later numeric-ID lookups still work.
    """

    def __init__(self, session: Path) -> None:
        self._opened_at = int(time.time())
        super().__init__(str(session))  # Telethon derives the file name ("<session>.session")

    def _cursor(self) -> sqlite3.Cursor:
        if self._conn is None:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            if os.path.exists(self.filename):
                source_uri = f"{Path(self.filename).resolve().as_uri()}?mode=ro"
                with closing(sqlite3.connect(source_uri, uri=True, timeout=5)) as source:
                    source.backup(self._conn)
        return self._conn.cursor()

    def clone(self, to_instance: Any = None) -> SQLiteSession:
        # CDN downloads clone the session; a plain in-memory one is what SQLiteSession gives.
        return super().clone(to_instance or SQLiteSession())

    def close(self) -> None:
        if self._conn is None:
            return
        columns = "id, hash, username, phone, name, date"
        learned = self._conn.execute(
            f"SELECT {columns} FROM entities WHERE date >= ?", (self._opened_at,)
        ).fetchall()
        self._conn.close()
        self._conn = None
        if not learned or not os.path.exists(self.filename):
            return
        # Only a lookup cache: skip it rather than fail when the listener holds the lock.
        with suppress(sqlite3.Error), closing(sqlite3.connect(self.filename, timeout=5)) as target:
            target.executemany(
                f"INSERT OR REPLACE INTO entities ({columns}) VALUES (?, ?, ?, ?, ?, ?)", learned
            )
            target.commit()


def _client(
    credentials: Credentials,
    session: Path,
    *,
    own_session: bool = False,
    receive_updates: bool = False,
    auto_reconnect: bool = False,
) -> TelegramClient:
    secure_data_directory()
    return TelegramClient(
        str(session) if own_session else _SessionSnapshot(session),
        credentials.api_id,
        credentials.api_hash,
        connection_retries=2,
        request_retries=2,
        retry_delay=1,
        flood_sleep_threshold=0,
        auto_reconnect=auto_reconnect,
        receive_updates=receive_updates,
        timeout=10,
    )


@asynccontextmanager
async def authorized_client(
    credentials: Credentials,
    session: Path,
    *,
    receive_updates: bool = False,
    own_session: bool = False,
) -> AsyncIterator[TelegramClient]:
    """Connected, authorized client; ``receive_updates`` also keeps it reconnecting."""
    client = _client(
        credentials,
        session,
        own_session=own_session,
        receive_updates=receive_updates,
        auto_reconnect=receive_updates,
    )
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
    client = _client(credentials, session, own_session=True, receive_updates=True)
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
