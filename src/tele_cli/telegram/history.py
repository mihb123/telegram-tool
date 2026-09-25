"""`tele get`: serve the latest messages from the local store and ask Telegram only for gaps.

Coverage lives in two tables: ``sync_state.head_message_id`` is the newest message as of
``checked_at`` and ``sync_ranges`` lists message-ID ranges stored without gaps. The
latest N messages come straight from the store — no connection at all — when:

* the head was checked less than ``max_age`` seconds ago,
* nothing newer was stored since (e.g. by `tele send` or `tele wait`), and
* the range reaching the head holds N messages or starts at the beginning of the chat.

Otherwise a single connection fetches only what is missing: messages newer than the head
(at most N), then older ones below the head range if fewer than N are stored. `--refresh`
re-fetches the whole window instead, to pick up edits and deletions.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from telethon import TelegramClient

from ..config import Credentials
from ..store import Store
from ..store.payloads import messages_payload, peer_payload
from ..store.repository import MAX_MESSAGE_ID
from ..values import from_iso, parse_target, utc_now
from .client import authorized_client, resolve_chat
from .media import MediaDownloader, prepare_media_directory
from .records import message_record


def _is_fresh(store: Store, chat_id: int, max_age: int) -> bool:
    state = store.sync_state(chat_id)
    if state is None or max_age <= 0:
        return False
    if from_iso(state["checked_at"]) < utc_now() - timedelta(seconds=max_age):
        return False
    newest = store.newest_message_id(chat_id)
    return newest is None or newest <= state["head_message_id"]


def _covers_latest(store: Store, chat_id: int, limit: int) -> bool:
    head = store.head_range(chat_id)
    if head is None:
        return False
    start, end = head
    return start == 0 or store.count_messages(chat_id, start, end) >= limit


def _latest_window(store: Store, chat_id: int, limit: int) -> list[Any]:
    head = store.head_range(chat_id)
    return store.latest_messages(chat_id, limit, min_id=head[0] if head else 0)


def _media_ids(rows: list[Any]) -> list[int]:
    return [row["id"] for row in rows if row["media_type"]]


class _Sync:
    """Fetches message spans from Telegram and records them as gap-free coverage."""

    def __init__(self, client: TelegramClient, store: Store, input_entity: Any, chat_id: int):
        self.client = client
        self.store = store
        self.input_entity = input_entity
        self.chat_id = chat_id
        self.fetched: dict[int, Any] = {}

    async def _collect(self, **kwargs: Any) -> list[Any]:
        batch = [
            message async for message in self.client.iter_messages(self.input_entity, **kwargs)
        ]
        self.fetched.update((message.id, message) for message in batch)
        return batch

    def _save(
        self,
        batch: list[Any],
        *,
        complete_from: int,
        complete_to: int,
        cover: tuple[int, int],
        head: int | None = None,
    ) -> None:
        """Telegram returned every message with complete_from <= id <= complete_to."""
        with self.store.transaction():
            self.store.lock_chat(self.chat_id)
            self.store.delete_messages_except(
                self.chat_id, complete_from, complete_to, [message.id for message in batch]
            )
            self.store.save_messages(self.chat_id, [message_record(message) for message in batch])
            self.store.add_range(self.chat_id, *cover)
            if head is not None:
                self.store.set_head(self.chat_id, head)

    async def newer(self, limit: int) -> None:
        state = self.store.sync_state(self.chat_id)
        after = state["head_message_id"] if state and self.store.head_range(self.chat_id) else 0
        batch = await self._collect(limit=limit, min_id=after)
        newest = batch[0].id if batch else after
        # A full batch may leave a gap above `after`; the new range then stands on its own.
        complete = len(batch) < limit
        low = after if complete else batch[-1].id
        self._save(
            batch,
            complete_from=after + 1 if complete else low,
            complete_to=MAX_MESSAGE_ID,
            cover=(low, newest),
            head=newest,
        )

    async def older(self, limit: int) -> None:
        head = self.store.head_range(self.chat_id)
        if head is None or head[0] == 0:
            return
        need = limit - self.store.count_messages(self.chat_id, *head)
        if need <= 0:
            return
        start = head[0]
        batch = await self._collect(limit=need, offset_id=start)
        low = 0 if len(batch) < need else batch[-1].id  # 0: reached the start of the chat
        self._save(batch, complete_from=low, complete_to=start - 1, cover=(low, start))

    async def refresh(self, limit: int) -> None:
        batch = await self._collect(limit=limit)
        low = 0 if len(batch) < limit else batch[-1].id
        newest = batch[0].id if batch else 0
        self._save(
            batch,
            complete_from=low,
            complete_to=MAX_MESSAGE_ID,
            cover=(low, newest),
            head=newest,
        )


def _payload(
    store: Store,
    chat: Any,
    rows: list[Any],
    *,
    target_value: str,
    limit: int,
    source: str,
    fetched: int,
    downloader: MediaDownloader | None,
) -> dict[str, Any]:
    messages = messages_payload(store, rows)
    state = store.sync_state(chat["id"])
    query: dict[str, Any] = {"target": target_value, "limit": limit}
    if downloader is not None:
        query.update(download_media=True, max_media_bytes=downloader.max_bytes)
        for message in messages:
            status = downloader.statuses.get(message["id"])
            if status and "media" in message:
                message["media"]["download_status"] = status
                if status == "skipped_too_large":
                    message["media"]["max_size_bytes"] = downloader.max_bytes
    payload: dict[str, Any] = {
        "ok": True,
        "query": query,
        "chat": peer_payload(chat),
        "cache": {
            "source": source,
            "synced_at": state["checked_at"] if state else None,
            "fetched_from_telegram": fetched,
        },
        "count": len(messages),
        "messages": messages,
    }
    if downloader is not None:
        payload["media_directory"] = str(downloader.directory)
        payload["media_summary"] = downloader.summary()
    return payload


async def get_messages(
    credentials: Credentials,
    session: Path,
    store: Store,
    target_value: str,
    limit: int,
    *,
    download_media: bool,
    download_dir: Path | None,
    max_media_bytes: int,
    max_age: int,
    refresh: bool,
) -> dict[str, Any]:
    directory = prepare_media_directory(target_value, download_dir) if download_media else None

    def downloader_for(chat_id: int) -> MediaDownloader | None:
        if directory is None:
            return None
        return MediaDownloader(
            store,
            chat_id,
            directory,
            max_bytes=max_media_bytes,
            exact_directory=download_dir is not None,
        )

    # Without a known account (first run on this machine) nothing local can be trusted yet.
    chat_row = store.find_peer(parse_target(target_value)) if store.account_id else None
    if (
        chat_row is not None
        and not refresh
        and _is_fresh(store, chat_row["id"], max_age)
        and _covers_latest(store, chat_row["id"], limit)
    ):
        rows = _latest_window(store, chat_row["id"], limit)
        downloader = downloader_for(chat_row["id"])
        if downloader is None or not downloader.resolve_locally(_media_ids(rows)):
            if downloader is not None:
                rows = _latest_window(store, chat_row["id"], limit)  # pick up resolved paths
            return _payload(
                store,
                chat_row,
                rows,
                target_value=target_value,
                limit=limit,
                source="local",
                fetched=0,
                downloader=downloader,
            )
        # Some attachments were never downloaded: fall through and fetch just those.

    async with authorized_client(credentials, session) as client:
        input_entity, chat = await resolve_chat(client, store, target_value)
        sync = _Sync(client, store, input_entity, chat["id"])
        if refresh:
            await sync.refresh(limit)
        else:
            if not _is_fresh(store, chat["id"], max_age):
                await sync.newer(limit)
            await sync.older(limit)
        rows = _latest_window(store, chat["id"], limit)
        downloader = downloader_for(chat["id"])
        if downloader is not None and downloader.resolve_locally(_media_ids(rows)):
            await downloader.download(client, input_entity, sync.fetched)
            rows = _latest_window(store, chat["id"], limit)

    return _payload(
        store,
        chat,
        rows,
        target_value=target_value,
        limit=limit,
        source="telegram",
        fetched=len(sync.fetched),
        downloader=downloader,
    )
