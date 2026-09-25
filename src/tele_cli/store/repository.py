from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ..config import Node, current_node
from ..errors import TeleError
from ..values import to_iso, utc_now
from .database import SCHEMA_VERSION, Database, open_database

# Upper bound for "every message newer than X" ranges.
MAX_MESSAGE_ID = 2**63 - 1
LISTENER_STALE_SECONDS = 30
LISTENER_LEASE_EXIT_CODE = 11

MESSAGE_COLUMNS = """
m.chat_id, m.id, m.date, m.edit_date, m.sender_id, m.outgoing, m.text,
       m.reply_to_message_id, m.grouped_id, m.service_action, m.meta,
       c.name AS chat_name, c.username AS chat_username,
       s.username AS sender_username, s.name AS sender_name,
       md.type AS media_type, md.kind AS media_kind, md.name AS media_name,
       md.mime_type AS media_mime_type, md.size AS media_size, md.width AS media_width,
       md.height AS media_height, md.duration AS media_duration,
       md.download_status AS media_download_status, md.download_error AS media_download_error
"""

MESSAGE_JOINS = """
JOIN peers c ON c.id = m.chat_id
LEFT JOIN peers s ON s.id = m.sender_id
LEFT JOIN media md
    ON md.account_id = m.account_id AND md.chat_id = m.chat_id AND md.message_id = m.id
"""

MESSAGE_SELECT = f"SELECT {MESSAGE_COLUMNS} FROM messages m {MESSAGE_JOINS}"
INBOX_SELECT = f"""
SELECT i.event_id, i.received_at, {MESSAGE_COLUMNS}
FROM inbox_events i
JOIN messages m
    ON m.account_id = i.account_id AND m.chat_id = i.chat_id AND m.id = i.message_id
{MESSAGE_JOINS}
"""

UPSERT_PEER = """
INSERT INTO peers (id, type, username, name, updated_at)
VALUES (:id, :type, :username, :name, :now)
ON CONFLICT (id) DO UPDATE SET
    type = excluded.type,
    username = excluded.username,
    name = excluded.name,
    updated_at = excluded.updated_at
"""

UPSERT_MESSAGE = """
INSERT INTO messages (account_id, chat_id, id, date, edit_date, sender_id, outgoing, text,
                      reply_to_message_id, grouped_id, service_action, meta, fetched_at)
VALUES (:account_id, :chat_id, :id, :date, :edit_date, :sender_id, :outgoing, :text,
        :reply_to_message_id, :grouped_id, :service_action, :meta, :now)
ON CONFLICT (account_id, chat_id, id) DO UPDATE SET
    date = excluded.date,
    edit_date = excluded.edit_date,
    sender_id = excluded.sender_id,
    outgoing = excluded.outgoing,
    text = excluded.text,
    reply_to_message_id = excluded.reply_to_message_id,
    grouped_id = excluded.grouped_id,
    service_action = excluded.service_action,
    meta = excluded.meta,
    fetched_at = excluded.fetched_at
"""

UPSERT_MEDIA = """
INSERT INTO media (account_id, chat_id, message_id, type, kind, file_key, name, extension,
                   mime_type, size, width, height, duration)
VALUES (:account_id, :chat_id, :message_id, :type, :kind, :file_key, :name, :extension,
        :mime_type, :size, :width, :height, :duration)
ON CONFLICT (account_id, chat_id, message_id) DO UPDATE SET
    type = excluded.type,
    kind = excluded.kind,
    file_key = excluded.file_key,
    name = excluded.name,
    extension = excluded.extension,
    mime_type = excluded.mime_type,
    size = excluded.size,
    width = excluded.width,
    height = excluded.height,
    duration = excluded.duration
"""

MESSAGE_FIELDS = (
    "id",
    "date",
    "edit_date",
    "sender_id",
    "text",
    "reply_to_message_id",
    "grouped_id",
    "service_action",
)
MEDIA_FIELDS = (
    "type",
    "kind",
    "file_key",
    "name",
    "extension",
    "mime_type",
    "size",
    "width",
    "height",
    "duration",
)


@dataclass
class MessageQuery:
    """Filters for `Store.messages`. Without ``after_id`` the newest matches win."""

    chat_id: int | None = None
    sender_id: int | None = None
    before_id: int | None = None
    after_id: int | None = None
    since: str | None = None
    until: str | None = None
    outgoing: bool | None = None
    with_media: bool = False
    terms: tuple[str, ...] = ()
    limit: int = 20


def _marks(values: list[Any]) -> str:
    return ", ".join(["?"] * len(values))


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class Store:
    """All reads and writes of the message store, scoped to one Telegram account.

    ``account_id`` is the account logged in on this node (host + OS user), remembered in
    ``nodes``; it stays ``None`` until this node first talks to Telegram.
    """

    def __init__(self, db: Database, node: Node) -> None:
        self.db = db
        self.node = node
        self.account_id: int | None = db.scalar(
            "SELECT account_id FROM nodes WHERE host = ? AND os_user = ?", (node.host, node.user)
        )

    @classmethod
    def open(cls) -> Store:
        db = open_database()
        try:
            db.migrate()
            return cls(db, current_node())
        except BaseException:
            db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def transaction(self) -> Any:
        return self.db.transaction()

    @property
    def account(self) -> int:
        if self.account_id is None:
            raise RuntimeError("Store.account_id must be set before reading or writing messages")
        return self.account_id

    # --- nodes & accounts ----------------------------------------------------------------

    def register_node(self, account: dict[str, Any]) -> None:
        """Remember that this node is logged in to ``account`` (a peer record)."""
        with self.transaction():
            self.save_peers([account])
            self.db.execute(
                """
                INSERT INTO nodes (host, os_user, account_id, last_seen_at) VALUES (?, ?, ?, ?)
                ON CONFLICT (host, os_user) DO UPDATE SET
                    account_id = excluded.account_id,
                    last_seen_at = excluded.last_seen_at
                """,
                (self.node.host, self.node.user, account["id"], to_iso(utc_now())),
            )
        self.account_id = account["id"]

    def touch_node(self) -> None:
        self.db.execute(
            "UPDATE nodes SET last_seen_at = ? WHERE host = ? AND os_user = ?",
            (to_iso(utc_now()), self.node.host, self.node.user),
        )

    def require_account(self) -> int:
        """This node's account, else the only account in the database (for read-only use)."""
        if self.account_id is not None:
            return self.account_id
        accounts = [
            row["account_id"]
            for row in self.db.all(
                "SELECT DISTINCT account_id FROM nodes WHERE account_id IS NOT NULL"
            )
        ]
        if len(accounts) == 1:
            self.account_id = accounts[0]
            return self.account_id
        if not accounts:
            raise TeleError(
                "no_account",
                "No Telegram account has stored messages in this database yet.",
                exit_code=5,
                hint="Run `tele status` (or any `tele get`) on a machine logged in to Telegram.",
            )
        raise TeleError(
            "ambiguous_account",
            f"This machine has no Telegram session; the database holds accounts {accounts}.",
            exit_code=2,
            hint="Choose one with `tele-local --account ID ...`.",
        )

    def nodes(self) -> list[Any]:
        return self.db.all("SELECT * FROM nodes ORDER BY last_seen_at DESC")

    # --- peers & dialogs -----------------------------------------------------------------

    def save_peers(self, peers: Iterable[dict[str, Any]]) -> None:
        now = to_iso(utc_now())
        self.db.executemany(
            UPSERT_PEER,
            [
                {
                    "id": peer["id"],
                    "type": peer["type"],
                    "username": peer.get("username"),
                    "name": peer.get("name"),
                    "now": now,
                }
                for peer in peers
            ],
        )

    def find_peer(self, target: str | int) -> Any | None:
        """Resolve a parsed `-u` target ("me", peer ID or username) without Telegram."""
        if target == "me":
            if self.account_id is None:
                return None
            target = self.account_id
        if isinstance(target, int):
            return self.db.one("SELECT * FROM peers WHERE id = ?", (target,))
        return self.db.one(
            "SELECT * FROM peers WHERE lower(username) = lower(?) ORDER BY updated_at DESC LIMIT 1",
            (target,),
        )

    def save_dialogs(self, dialogs: list[tuple[dict[str, Any], int, str | None]]) -> None:
        """``(peer, unread_count, last_message_date)`` for each dialog."""
        now = to_iso(utc_now())
        with self.transaction():
            self.save_peers(peer for peer, _, _ in dialogs)
            self.db.executemany(
                """
                INSERT INTO dialogs (account_id, peer_id, unread_count, last_message_date,
                                     updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (account_id, peer_id) DO UPDATE SET
                    unread_count = excluded.unread_count,
                    last_message_date = excluded.last_message_date,
                    updated_at = excluded.updated_at
                """,
                [(self.account, peer["id"], unread, last, now) for peer, unread, last in dialogs],
            )

    # --- messages & media ----------------------------------------------------------------

    def save_messages(self, chat_id: int, records: Iterable[dict[str, Any]]) -> None:
        """Upsert records from ``telegram.records.message_record`` in a few batched statements.

        Recorded downloads survive re-fetching unless the attachment itself changed.
        """
        records = list(records)
        if not records:
            return
        key = {"account_id": self.account, "chat_id": chat_id}
        now = to_iso(utc_now())
        senders = {
            record["sender"]["id"]: record["sender"] for record in records if record["sender"]
        }
        with self.transaction():
            self.save_peers(senders.values())
            self.db.executemany(
                UPSERT_MESSAGE,
                [
                    {
                        **key,
                        **{field: record[field] for field in MESSAGE_FIELDS},
                        "outgoing": int(record["outgoing"]),
                        "meta": json.dumps(record["meta"], ensure_ascii=False)
                        if record.get("meta")
                        else None,
                        "now": now,
                    }
                    for record in records
                ],
            )
            plain = [record["id"] for record in records if record["media"] is None]
            if plain:
                self.db.execute(
                    "DELETE FROM media WHERE account_id = ? AND chat_id = ? "
                    f"AND message_id IN ({_marks(plain)})",
                    (self.account, chat_id, *plain),
                )
            attached = {record["id"]: record["media"] for record in records if record["media"]}
            changed = [
                row["message_id"]
                for row in self.media_rows(chat_id, list(attached))
                if row["file_key"] != attached[row["message_id"]]["file_key"]
            ]
            if changed:  # a different file now: forget old copies and outcomes
                where = f"account_id = ? AND chat_id = ? AND message_id IN ({_marks(changed)})"
                params = (self.account, chat_id, *changed)
                self.db.execute(f"DELETE FROM media_files WHERE {where}", params)
                self.db.execute(
                    f"UPDATE media SET download_status = NULL, download_error = NULL WHERE {where}",
                    params,
                )
            self.db.executemany(
                UPSERT_MEDIA,
                [
                    {**key, "message_id": message_id, **{f: media.get(f) for f in MEDIA_FIELDS}}
                    for message_id, media in attached.items()
                ],
            )

    def save_inbox_message(self, chat: dict[str, Any], record: dict[str, Any]) -> int | None:
        """Persist one listener message and allocate its durable account-wide cursor."""
        chat_id, message_id = chat["id"], record["id"]
        with self.transaction():
            self.db.lock(f"tele:inbox:{self.account}")
            self.save_peers([chat])
            self.save_messages(chat_id, [record])
            existing = self.db.scalar(
                "SELECT event_id FROM inbox_events "
                "WHERE account_id = ? AND chat_id = ? AND message_id = ?",
                (self.account, chat_id, message_id),
            )
            if existing is not None:
                return None
            self.db.execute(
                "INSERT INTO inbox_sequence (account_id, last_event_id) VALUES (?, 1) "
                "ON CONFLICT (account_id) DO UPDATE "
                "SET last_event_id = inbox_sequence.last_event_id + 1",
                (self.account,),
            )
            event_id = self.latest_inbox_event_id()
            self.db.execute(
                "INSERT INTO inbox_events "
                "(account_id, event_id, chat_id, message_id, received_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (self.account, event_id, chat_id, message_id, to_iso(utc_now())),
            )
            return event_id

    # --- listener lifecycle ---------------------------------------------------------------

    def _listener_already_running(self, active: Any) -> TeleError:
        owner = f"{active['os_user']}@{active['host']} (pid {active['process_id']})"
        return TeleError(
            "listener_already_running",
            f"A healthy listener already owns this Telegram account: {owner}.",
            exit_code=LISTENER_LEASE_EXIT_CODE,
            hint="Use `tele-local listeners` to inspect it; do not start duplicates.",
        )

    def _local_listener_is_dead(self, active: Any) -> bool:
        if active["host"] != self.node.host or active["os_user"] != self.node.user:
            return False
        try:
            os.kill(active["process_id"], 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    def active_listener(self) -> Any | None:
        """The account's lease with a recent heartbeat, ignoring crashed processes on this node."""
        cutoff = to_iso(utc_now() - timedelta(seconds=LISTENER_STALE_SECONDS))
        rows = self.db.all(
            "SELECT host, os_user, process_id FROM listeners "
            "WHERE account_id = ? AND heartbeat_at >= ?",
            (self.account, cutoff),
        )
        return next((row for row in rows if not self._local_listener_is_dead(row)), None)

    def assert_listener_available(self) -> None:
        """Fail before opening Telegram when another listener heartbeat is still healthy."""
        if self.account_id is None:
            return
        if (active := self.active_listener()) is not None:
            raise self._listener_already_running(active)

    def claim_listener(self, process_id: int) -> None:
        """Claim the account listener lease, rejecting another recently healthy process."""
        with self.transaction():
            self.db.lock(f"tele:listener:{self.account}")
            if (active := self.active_listener()) is not None:
                raise self._listener_already_running(active)
            # Every remaining lease is stale or belongs to a dead local process.
            self.db.execute("DELETE FROM listeners WHERE account_id = ?", (self.account,))
            timestamp = to_iso(utc_now())
            self.db.execute(
                "INSERT INTO listeners "
                "(account_id, host, os_user, process_id, started_at, heartbeat_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self.account,
                    self.node.host,
                    self.node.user,
                    process_id,
                    timestamp,
                    timestamp,
                ),
            )

    def heartbeat_listener(self, process_id: int) -> None:
        """Refresh this process's listener lease so duplicate daemons remain detectable."""
        updated = self.db.execute(
            "UPDATE listeners SET heartbeat_at = ? "
            "WHERE account_id = ? AND host = ? AND os_user = ? AND process_id = ?",
            (
                to_iso(utc_now()),
                self.account,
                self.node.host,
                self.node.user,
                process_id,
            ),
        )
        if updated != 1:
            raise TeleError(
                "listener_lease_lost",
                "This process no longer owns the Telegram listener lease.",
                exit_code=LISTENER_LEASE_EXIT_CODE,
                hint="The listener will stop so only the current lease owner remains active.",
            )

    def release_listener(self, process_id: int) -> None:
        """Release this process's listener lease after a graceful shutdown."""
        self.db.execute(
            "DELETE FROM listeners "
            "WHERE account_id = ? AND host = ? AND os_user = ? AND process_id = ?",
            (self.account, self.node.host, self.node.user, process_id),
        )

    def listeners(self) -> list[Any]:
        return self.db.all(
            "SELECT account_id, host, os_user, process_id, started_at, heartbeat_at "
            "FROM listeners ORDER BY heartbeat_at DESC"
        )

    def delete_messages_except(self, chat_id: int, low: int, high: int, keep: list[int]) -> None:
        """Drop local messages in [low, high] that Telegram no longer returns (deleted)."""
        sql = "DELETE FROM messages WHERE account_id = ? AND chat_id = ? AND id BETWEEN ? AND ?"
        if keep:
            sql += f" AND id NOT IN ({_marks(keep)})"
        self.db.execute(sql, (self.account, chat_id, low, high, *keep))

    def media_rows(self, chat_id: int, message_ids: list[int]) -> list[Any]:
        if not message_ids:
            return []
        return self.db.all(
            "SELECT * FROM media WHERE account_id = ? AND chat_id = ? "
            f"AND message_id IN ({_marks(message_ids)}) ORDER BY message_id DESC",
            (self.account, chat_id, *message_ids),
        )

    def node_copies(self, chat_id: int, message_id: int, file_key: str | None) -> list[Any]:
        """Files on this node for the message, then for the same Telegram file anywhere."""
        own = self.db.all(
            "SELECT path, size FROM media_files WHERE account_id = ? AND chat_id = ? "
            "AND message_id = ? AND host = ? AND os_user = ?",
            (self.account, chat_id, message_id, self.node.host, self.node.user),
        )
        if not file_key:
            return own
        return own + self.db.all(
            """
            SELECT f.path, f.size FROM media md
            JOIN media_files f ON f.account_id = md.account_id AND f.chat_id = md.chat_id
                AND f.message_id = md.message_id
            WHERE md.file_key = ? AND f.host = ? AND f.os_user = ?
            """,
            (file_key, self.node.host, self.node.user),
        )

    def record_file(self, chat_id: int, message_id: int, path: str, size: int, status: str) -> None:
        """Record that this node (hostname + OS user) keeps the message's file at ``path``."""
        with self.transaction():
            self.db.execute(
                """
                INSERT INTO media_files (account_id, chat_id, message_id, host, os_user, path,
                                         size, downloaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (account_id, chat_id, message_id, host, os_user) DO UPDATE SET
                    path = excluded.path,
                    size = excluded.size,
                    downloaded_at = excluded.downloaded_at
                """,
                (
                    self.account,
                    chat_id,
                    message_id,
                    self.node.host,
                    self.node.user,
                    path,
                    size,
                    to_iso(utc_now()),
                ),
            )
            self.set_media_status(chat_id, message_id, status)

    def set_media_status(
        self, chat_id: int, message_id: int, status: str, error: str | None = None
    ) -> None:
        self.db.execute(
            "UPDATE media SET download_status = ?, download_error = ? "
            "WHERE account_id = ? AND chat_id = ? AND message_id = ?",
            (status, error, self.account, chat_id, message_id),
        )

    def files_for(self, keys: Iterable[tuple[int, int]]) -> dict[tuple[int, int], list[Any]]:
        """Stored copies on every node, per ``(chat_id, message_id)``."""
        keys = list(dict.fromkeys(keys))
        files: dict[tuple[int, int], list[Any]] = {}
        if not keys:
            return files
        pairs = ", ".join(["(?, ?)"] * len(keys))
        rows = self.db.all(
            "SELECT chat_id, message_id, host, os_user, path FROM media_files "
            f"WHERE account_id = ? AND (chat_id, message_id) IN (VALUES {pairs}) "
            "ORDER BY downloaded_at",
            (self.account, *(value for key in keys for value in key)),
        )
        for row in rows:
            files.setdefault((row["chat_id"], row["message_id"]), []).append(row)
        return files

    # --- sync coverage -------------------------------------------------------------------

    def lock_chat(self, chat_id: int) -> None:
        """Keep machines syncing the same chat from interleaving their writes."""
        self.db.lock(f"tele:sync:{self.account}:{chat_id}")

    def sync_state(self, chat_id: int) -> Any | None:
        return self.db.one(
            "SELECT * FROM sync_state WHERE account_id = ? AND chat_id = ?",
            (self.account, chat_id),
        )

    def set_head(self, chat_id: int, head_message_id: int) -> None:
        self.db.execute(
            """
            INSERT INTO sync_state (account_id, chat_id, head_message_id, checked_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (account_id, chat_id) DO UPDATE SET
                head_message_id = excluded.head_message_id,
                checked_at = excluded.checked_at
            """,
            (self.account, chat_id, head_message_id, to_iso(utc_now())),
        )

    def add_range(self, chat_id: int, start_id: int, end_id: int) -> None:
        """Record [start_id, end_id] as stored without gaps, merging overlapping ranges."""
        where = "account_id = ? AND chat_id = ? AND start_id <= ? AND end_id >= ?"
        overlap = (self.account, chat_id, end_id, start_id)
        for row in self.db.all(f"SELECT start_id, end_id FROM sync_ranges WHERE {where}", overlap):
            start_id = min(start_id, row["start_id"])
            end_id = max(end_id, row["end_id"])
        self.db.execute(f"DELETE FROM sync_ranges WHERE {where}", overlap)
        self.db.execute(
            "INSERT INTO sync_ranges (account_id, chat_id, start_id, end_id) VALUES (?, ?, ?, ?)",
            (self.account, chat_id, start_id, end_id),
        )

    def head_range(self, chat_id: int) -> tuple[int, int] | None:
        """The gap-free range that reaches the chat's newest known message."""
        row = self.db.one(
            """
            SELECT r.start_id, r.end_id FROM sync_state s
            JOIN sync_ranges r ON r.account_id = s.account_id AND r.chat_id = s.chat_id
                AND r.start_id <= s.head_message_id AND r.end_id >= s.head_message_id
            WHERE s.account_id = ? AND s.chat_id = ?
            """,
            (self.account, chat_id),
        )
        return (row["start_id"], row["end_id"]) if row else None

    def count_messages(self, chat_id: int, start_id: int, end_id: int) -> int:
        return self.db.scalar(
            "SELECT COUNT(*) FROM messages WHERE account_id = ? AND chat_id = ? "
            "AND id BETWEEN ? AND ?",
            (self.account, chat_id, start_id, end_id),
        )

    def newest_message_id(self, chat_id: int) -> int | None:
        return self.db.scalar(
            "SELECT MAX(id) FROM messages WHERE account_id = ? AND chat_id = ?",
            (self.account, chat_id),
        )

    # --- reads ---------------------------------------------------------------------------

    def latest_messages(self, chat_id: int, limit: int, *, min_id: int = 0) -> list[Any]:
        return self.db.all(
            f"{MESSAGE_SELECT} WHERE m.account_id = ? AND m.chat_id = ? AND m.id >= ? "
            "ORDER BY m.id DESC LIMIT ?",
            (self.account, chat_id, min_id, limit),
        )

    def latest_inbox_event_id(self) -> int:
        """Newest cursor ever handed out; a cheap primary-key lookup, safe to poll."""
        return (
            self.db.scalar(
                "SELECT last_event_id FROM inbox_sequence WHERE account_id = ?", (self.account,)
            )
            or 0
        )

    def inbox_events(
        self,
        *,
        after_event_id: int | None,
        chat_id: int | None,
        limit: int,
        sender_id: int | None = None,
    ) -> list[Any]:
        """Listener events, newest first for browsing or oldest first after a cursor."""
        clauses = ["i.account_id = ?"]
        params: list[Any] = [self.account]
        if after_event_id is not None:
            clauses.append("i.event_id > ?")
            params.append(after_event_id)
        if chat_id is not None:
            clauses.append("i.chat_id = ?")
            params.append(chat_id)
        if sender_id is not None:
            clauses.append("m.sender_id = ?")
            params.append(sender_id)
        direction = "ASC" if after_event_id is not None else "DESC"
        return self.db.all(
            f"{INBOX_SELECT} WHERE {' AND '.join(clauses)} ORDER BY i.event_id {direction} LIMIT ?",
            [*params, limit],
        )

    def consumer_cursor(self, name: str) -> int | None:
        return self.db.scalar(
            "SELECT last_event_id FROM inbox_consumers WHERE account_id = ? AND name = ?",
            (self.account, name),
        )

    def save_consumer_cursor(self, name: str, event_id: int) -> None:
        self.db.execute(
            """
            INSERT INTO inbox_consumers (account_id, name, last_event_id, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (account_id, name) DO UPDATE SET
                last_event_id = excluded.last_event_id,
                updated_at = excluded.updated_at
            """,
            (self.account, name, event_id, to_iso(utc_now())),
        )

    def message(self, chat_id: int, message_id: int) -> Any | None:
        return self.db.one(
            f"{MESSAGE_SELECT} WHERE m.account_id = ? AND m.chat_id = ? AND m.id = ?",
            (self.account, chat_id, message_id),
        )

    def messages(self, query: MessageQuery) -> list[Any]:
        """Matching messages, newest first (oldest first when ``after_id`` is set)."""
        clauses = ["m.account_id = ?"]
        params: list[Any] = [self.account]

        def where(clause: str, *values: Any) -> None:
            clauses.append(clause)
            params.extend(values)

        if query.chat_id is not None:
            where("m.chat_id = ?", query.chat_id)
        if query.sender_id is not None:
            where("m.sender_id = ?", query.sender_id)
        if query.before_id is not None:
            where("m.id < ?", query.before_id)
        if query.after_id is not None:
            where("m.id > ?", query.after_id)
        if query.since:
            where("m.date >= ?", query.since)
        if query.until:
            where("m.date < ?", query.until)
        if query.outgoing is not None:
            where("m.outgoing = ?", int(query.outgoing))
        if query.with_media:
            where("md.type IS NOT NULL")
        for term in query.terms:
            pattern = _like_pattern(term)
            where(
                "(fold(m.text) LIKE ? ESCAPE '\\' OR fold(md.name) LIKE ? ESCAPE '\\')",
                pattern,
                pattern,
            )
        direction = "ASC" if query.after_id is not None else "DESC"
        return self.db.all(
            f"{MESSAGE_SELECT} WHERE {' AND '.join(clauses)} "
            f"ORDER BY m.date {direction}, m.id {direction} LIMIT ?",
            [*params, query.limit],
        )

    def chats(self, limit: int) -> list[Any]:
        """Chats with stored messages or a dialog entry, most recently active first."""
        return self.db.all(
            """
            SELECT p.id, p.type, p.username, p.name,
                   (SELECT COUNT(*) FROM messages
                    WHERE account_id = :account AND chat_id = p.id) AS messages,
                   COALESCE((SELECT MAX(date) FROM messages
                             WHERE account_id = :account AND chat_id = p.id),
                            d.last_message_date) AS last_message_date,
                   d.unread_count, s.checked_at AS synced_at
            FROM peers p
            LEFT JOIN dialogs d ON d.account_id = :account AND d.peer_id = p.id
            LEFT JOIN sync_state s ON s.account_id = :account AND s.chat_id = p.id
            WHERE d.peer_id IS NOT NULL OR s.chat_id IS NOT NULL
               OR EXISTS (SELECT 1 FROM messages WHERE account_id = :account AND chat_id = p.id)
            ORDER BY last_message_date DESC NULLS LAST
            LIMIT :limit
            """,
            {"account": self.account, "limit": limit},
        )

    def media_list(
        self,
        *,
        chat_id: int | None,
        media_type: str | None,
        on_this_node: bool | None,
        limit: int,
    ) -> list[Any]:
        clauses, params = ["md.account_id = ?"], [self.account]
        if chat_id is not None:
            clauses.append("md.chat_id = ?")
            params.append(chat_id)
        if media_type:
            clauses.append("(lower(md.kind) = lower(?) OR lower(md.type) = lower(?))")
            params += [media_type, media_type]
        if on_this_node is not None:
            clauses.append(
                f"{'' if on_this_node else 'NOT '}EXISTS (SELECT 1 FROM media_files f "
                "WHERE f.account_id = md.account_id AND f.chat_id = md.chat_id "
                "AND f.message_id = md.message_id AND f.host = ? AND f.os_user = ?)"
            )
            params += [self.node.host, self.node.user]
        return self.db.all(
            f"""
            SELECT md.chat_id, md.message_id, m.date, c.name AS chat_name,
                   md.type AS media_type, md.kind AS media_kind, md.name AS media_name,
                   md.mime_type AS media_mime_type, md.size AS media_size,
                   md.width AS media_width, md.height AS media_height,
                   md.duration AS media_duration, md.download_status AS media_download_status,
                   md.download_error AS media_download_error
            FROM media md
            JOIN messages m
                ON m.account_id = md.account_id AND m.chat_id = md.chat_id AND m.id = md.message_id
            JOIN peers c ON c.id = md.chat_id
            WHERE {" AND ".join(clauses)}
            ORDER BY m.date DESC LIMIT ?
            """,
            [*params, limit],
        )

    def info(self) -> dict[str, Any]:
        counts = {
            table: self.db.scalar(f"SELECT COUNT(*) FROM {table}")
            for table in (
                "nodes",
                "peers",
                "dialogs",
                "messages",
                "media",
                "media_files",
                "inbox_events",
                "listeners",
            )
        }
        return {
            "backend": self.db.dialect,
            "database": self.db.label,
            "schema_version": SCHEMA_VERSION,
            "size_bytes": self.db.size_bytes(),
            "node": {"host": self.node.host, "user": self.node.user, "account_id": self.account_id},
            "counts": counts,
        }
