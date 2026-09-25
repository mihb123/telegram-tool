"""`tele-local`: query messages already fetched by `tele`, without contacting Telegram.

Reads the same database as `tele` (local SQLite, or the shared PostgreSQL server from
TELE_DATABASE_URL) and never imports Telethon.
"""

from __future__ import annotations

import argparse
import re
import time
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from .. import __version__
from ..config import display_timezone, listener_targets
from ..errors import TeleError
from ..output import render_messages, render_table
from ..store import LISTENER_STALE_SECONDS, MessageQuery, Store
from ..store.database import fold
from ..store.payloads import compact, media_payload, messages_payload, peer_payload
from ..values import display_time, from_iso, parse_target, utc_now
from .args import (
    add_format,
    add_meta,
    add_short_flags,
    add_target,
    bounded_int,
    positive_message_id,
    time_bound,
)
from .runner import run

LIMIT = bounded_int("limit", 1, 5000)
EVENT_CURSOR = bounded_int("event cursor", 0, 2**63 - 1)
WAIT_POLL_SECONDS = 0.5
LISTENER_CHECK_SECONDS = 10
DEFAULT_SETTLE_SECONDS = 30
# A busy group may never fall quiet: stop collecting this many settle periods after the first.
SETTLE_WINDOW_FACTOR = 4
_CONSUMER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")


def consumer_name(value: str) -> str:
    if not _CONSUMER_NAME.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "consumer name must be 1-64 letters, digits, '.', '_', ':' or '-'"
        )
    return value


def _add_time_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--since",
        type=time_bound(end_of_day=False),
        help="From this time: 2h, 3d, 2026-09-25 (dates and times in $TELE_TIMEZONE, like `time`)",
    )
    parser.add_argument(
        "--until",
        type=time_bound(end_of_day=True),
        help="Before this time; a bare date includes that whole day",
    )
    parser.add_argument(
        "--from",
        dest="sender",
        help="Only messages from this sender (username, ID or 'me')",
    )


def _add_inbox_selectors(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--from",
        dest="sender",
        help="Only messages from this sender (username, ID or 'me'), e.g. one person in a group",
    )
    parser.add_argument(
        "--consumer",
        type=consumer_name,
        help=(
            "Named cursor kept in the database: resume after it when --after is omitted and "
            "advance it past what is returned (a new name starts at the newest event)"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tele-local",
        description=(
            "Query the local store of Telegram messages fetched by `tele` "
            "(never contacts Telegram)."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--account",
        type=int,
        help="Telegram account ID to read (default: the account logged in on this machine)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    chats = subparsers.add_parser("chats", help="List stored chats, most recently active first")
    chats.add_argument(
        "-l",
        "--limit",
        type=LIMIT,
        default=50,
        help="Maximum number of chats to list (default: 50)",
    )
    add_format(chats)

    messages = subparsers.add_parser("messages", help="Messages of one chat, newest first")
    add_target(messages)
    messages.add_argument(
        "-l",
        "--limit",
        type=LIMIT,
        default=20,
        help="Maximum number of messages to return (default: 20)",
    )
    cursor = messages.add_mutually_exclusive_group()
    cursor.add_argument("--before", type=positive_message_id, help="Only IDs below this")
    cursor.add_argument(
        "--after", type=positive_message_id, help="The first messages with IDs above this"
    )
    cursor.add_argument(
        "--around", type=positive_message_id, help="This message with context on both sides"
    )
    _add_time_filters(messages)
    direction = messages.add_mutually_exclusive_group()
    direction.add_argument("--incoming", action="store_true", help="Only received messages")
    direction.add_argument("--outgoing", action="store_true", help="Only sent messages")
    messages.add_argument("--with-media", action="store_true", help="Only messages with media")
    messages.add_argument(
        "--oldest-first", action="store_true", help="Print in chronological order"
    )
    add_meta(messages)
    add_format(messages)

    search = subparsers.add_parser(
        "search",
        help="Find messages by text or file name (case- and accent-insensitive)",
    )
    search.add_argument("query", help="Words that must all appear, e.g. 'doi soat'")
    add_target(search, required=False)
    search.add_argument(
        "-l",
        "--limit",
        type=LIMIT,
        default=20,
        help="Maximum number of messages to return (default: 20)",
    )
    _add_time_filters(search)
    add_meta(search)
    add_format(search)

    media = subparsers.add_parser(
        "media", help="Attachments and which machines (host, user) hold their files"
    )
    add_target(media, required=False)
    media.add_argument(
        "-l",
        "--limit",
        type=LIMIT,
        default=50,
        help="Maximum number of media items to return (default: 50)",
    )
    media.add_argument(
        "--type",
        dest="media_type",
        help="photo, video, gif, sticker, voice, audio, video_note, file, location, ... "
        "(or a Telegram class: Photo, Document, WebPage)",
    )
    state = media.add_mutually_exclusive_group()
    state.add_argument(
        "--downloaded", action="store_true", help="Only media with a file on this machine"
    )
    state.add_argument("--missing", action="store_true", help="Only media not on this machine")
    add_format(media)

    inbox = subparsers.add_parser(
        "inbox", help="Incoming messages captured by `tele listen`, with durable event cursors"
    )
    add_target(inbox, required=False)
    inbox.add_argument(
        "-l",
        "--limit",
        type=LIMIT,
        default=20,
        help="Maximum number of messages to return (default: 20)",
    )
    inbox.add_argument(
        "--after",
        dest="after_event_id",
        type=EVENT_CURSOR,
        help="Only listener events after this account-wide event cursor",
    )
    _add_inbox_selectors(inbox)
    add_meta(inbox)
    add_format(inbox)

    wait = subparsers.add_parser(
        "wait", help="Wait locally for messages captured by `tele listen` (no Telegram connection)"
    )
    add_target(wait, required=False)
    wait.add_argument(
        "--after",
        dest="after_event_id",
        type=EVENT_CURSOR,
        help="Return stored events after this cursor; without it, wait only for future events",
    )
    _add_inbox_selectors(wait)
    wait.add_argument(
        "-l",
        "--limit",
        type=LIMIT,
        default=100,
        help="Maximum number of events to return (default: 100)",
    )
    wait.add_argument(
        "--timeout",
        dest="timeout_seconds",
        type=bounded_int("timeout", 1, 86_400),
        default=900,
        help="Maximum seconds to wait for the first message (default: 900; maximum: 86400)",
    )
    wait.add_argument(
        "-s",
        "--settle",
        dest="settle_seconds",
        type=bounded_int("settle", 0, 3600),
        default=DEFAULT_SETTLE_SECONDS,
        help=(
            "After a message arrives, keep collecting until none arrives for this many seconds, "
            f"at most {SETTLE_WINDOW_FACTOR}x that in total; 0 returns at once "
            f"(default: {DEFAULT_SETTLE_SECONDS})"
        ),
    )
    add_meta(wait)
    add_format(wait)

    listeners = subparsers.add_parser(
        "listeners", help="Show listener processes and whether their heartbeat is healthy"
    )
    add_format(listeners)

    sql = subparsers.add_parser(
        "sql",
        help="Run a read-only SQL query; fold(text) strips case and accents",
    )
    sql.add_argument("query", help='e.g. "SELECT id, text FROM messages WHERE chat_id = -123"')
    sql.add_argument(
        "--max-rows",
        type=LIMIT,
        default=200,
        help="Maximum number of rows to return (default: 200)",
    )
    add_format(sql)

    subparsers.add_parser(
        "info", help="Database, schema version, row counts and machines using the database"
    )
    return add_short_flags(parser)


def _peer(store: Store, target_value: str, role: str = "chat") -> Any:
    row = store.find_peer(parse_target(target_value))
    if row is None:
        raise TeleError(
            "not_cached",
            f"No local data for {role} {target_value!r}.",
            exit_code=5,
            hint=f"Run `tele get -u {target_value}` once to cache it, or `tele-local chats`.",
        )
    return row


def _query(store: Store, args: argparse.Namespace, **fields: Any) -> MessageQuery:
    sender = _peer(store, args.sender, "sender") if args.sender else None
    return MessageQuery(
        sender_id=sender["id"] if sender else None,
        since=args.since,
        until=args.until,
        limit=args.limit,
        **fields,
    )


def _messages(store: Store, args: argparse.Namespace) -> dict[str, Any]:
    chat = _peer(store, args.target)
    outgoing = True if args.outgoing else False if args.incoming else None
    query = _query(store, args, chat_id=chat["id"], outgoing=outgoing, with_media=args.with_media)
    if args.around:
        newer = args.limit // 2
        query.limit = args.limit - newer
        query.before_id = args.around + 1
        rows = store.messages(query)
        query.limit, query.before_id, query.after_id = newer, None, args.around
        rows += store.messages(query) if newer else []
    else:
        query.before_id, query.after_id = args.before, args.after
        rows = store.messages(query)
    rows.sort(key=lambda row: (row["date"], row["id"]), reverse=not args.oldest_first)
    state = store.sync_state(chat["id"])
    return {
        "ok": True,
        "chat": peer_payload(chat),
        "synced_at": display_time(state["checked_at"] if state else None, display_timezone()),
        "order": "oldest_first" if args.oldest_first else "newest_first",
        "count": len(rows),
        "messages": messages_payload(store, rows, meta=args.meta),
    }


def _search(store: Store, args: argparse.Namespace) -> dict[str, Any]:
    terms = tuple(fold(args.query).split())
    if not terms:
        raise TeleError("invalid_query", "Search query cannot be empty.", exit_code=2)
    chat = _peer(store, args.target) if args.target else None
    rows = store.messages(_query(store, args, chat_id=chat["id"] if chat else None, terms=terms))
    return {
        "ok": True,
        "query": compact({"text": args.query, "chat": args.target}),
        "count": len(rows),
        "messages": messages_payload(store, rows, include_chat=chat is None, meta=args.meta),
    }


def _media(store: Store, args: argparse.Namespace) -> dict[str, Any]:
    chat = _peer(store, args.target) if args.target else None
    rows = store.media_list(
        chat_id=chat["id"] if chat else None,
        media_type=args.media_type,
        on_this_node=True if args.downloaded else False if args.missing else None,
        limit=args.limit,
    )
    files = store.files_for((row["chat_id"], row["message_id"]) for row in rows)
    zone = display_timezone()
    items = [
        {
            **compact({"chat_id": row["chat_id"], "chat_name": row["chat_name"]}),
            "message_id": row["message_id"],
            "date": display_time(row["date"], zone),
            **media_payload(row, files.get((row["chat_id"], row["message_id"]), []), store.node),
        }
        for row in rows
    ]
    return {"ok": True, "count": len(items), "media": items}


def _inbox_payload(store: Store, rows: list[Any], baseline: int, *, meta: bool) -> dict[str, Any]:
    messages = messages_payload(store, rows, include_chat=True, meta=meta)
    zone = display_timezone()
    events = [
        {
            "event_id": row["event_id"],
            "received_at": display_time(row["received_at"], zone),
            **message,
        }
        for row, message in zip(rows, messages, strict=True)
    ]
    cursor = max((event["event_id"] for event in events), default=baseline)
    return {
        "ok": True,
        "cursor": {"after_event_id": cursor},
        "count": len(events),
        "messages": events,
    }


def _start_cursor(store: Store, args: argparse.Namespace) -> int | None:
    """``--after``, else the consumer's saved cursor; a new consumer starts at the newest event.

    The start is saved for the consumer right away, so nothing that arrives before the next
    call is skipped even when this one times out or fails.
    """
    cursor = args.after_event_id
    if args.consumer is None:
        return cursor
    if cursor is None:
        cursor = store.consumer_cursor(args.consumer)
    if cursor is None:
        cursor = store.latest_inbox_event_id()
    store.save_consumer_cursor(args.consumer, cursor)
    return cursor


def _sender_id(store: Store, args: argparse.Namespace) -> int | None:
    return _peer(store, args.sender, "sender")["id"] if args.sender else None


def _deliver(store: Store, args: argparse.Namespace, rows: list[Any], baseline: int) -> Any:
    payload = _inbox_payload(store, rows, baseline, meta=args.meta)
    if args.consumer is not None:
        store.save_consumer_cursor(args.consumer, payload["cursor"]["after_event_id"])
        payload["consumer"] = args.consumer
    return payload


def _inbox(store: Store, args: argparse.Namespace) -> dict[str, Any]:
    chat = _peer(store, args.target) if args.target else None
    after = _start_cursor(store, args)
    rows = store.inbox_events(
        after_event_id=after,
        chat_id=chat["id"] if chat else None,
        sender_id=_sender_id(store, args),
        limit=args.limit,
    )
    baseline = after if after is not None else store.latest_inbox_event_id()
    payload = _deliver(store, args, rows, baseline)
    payload["order"] = "oldest_first" if after is not None else "newest_first"
    return payload


def _listener_not_running(baseline: int) -> TeleError:
    return TeleError(
        "listener_not_running",
        "No healthy `tele listen` process is capturing messages for this account.",
        exit_code=12,
        hint="Start it (`make start-listener` or `tele listen`), then wait again from this cursor.",
        details={"cursor": {"after_event_id": baseline}},
    )


def _require_listened(store: Store, chat: Any, target_value: str) -> None:
    """Fail fast when TELE_LISTEN_CHATS excludes the chat, since waiting could only time out.

    Entries match by peer ID or username either way round, through the cached peer row.
    An unset whitelist (a machine that only reads the shared store) skips the check.
    """
    targets = listener_targets()
    if not targets:
        return
    username = (chat["username"] or "").casefold()
    for value in targets:
        entry = parse_target(value)
        if entry == "me":
            entry = store.account_id
        if entry == chat["id"] or (isinstance(entry, str) and entry.casefold() == username):
            return
    raise TeleError(
        "chat_not_listened",
        f"Chat {target_value!r} is not in TELE_LISTEN_CHATS, so the listener never captures it.",
        exit_code=2,
        hint="Add its username or ID to TELE_LISTEN_CHATS and restart `tele listen`.",
        details={"chat": peer_payload(chat), "listen_chats": list(targets)},
    )


def _wait(store: Store, args: argparse.Namespace) -> dict[str, Any]:
    """Wait for events after the cursor, then keep collecting until the chats go quiet.

    Waiting is only meaningful while a listener captures messages, so a missing or dead
    listener, or a chat outside its whitelist, fails fast instead of ending in a timeout.
    """
    chat_id = None
    if args.target:
        chat = _peer(store, args.target)
        _require_listened(store, chat, args.target)
        chat_id = chat["id"]
    sender_id = _sender_id(store, args)
    baseline = _start_cursor(store, args)
    if baseline is None:
        baseline = store.latest_inbox_event_id()
    rows: list[Any] = []
    seen = baseline

    def poll() -> None:
        # Poll the cursor (a primary-key lookup); the inbox join runs only after it moves.
        nonlocal rows, seen
        latest = store.latest_inbox_event_id()
        if latest > seen:
            rows = store.inbox_events(
                after_event_id=baseline, chat_id=chat_id, sender_id=sender_id, limit=args.limit
            )
            seen = latest

    # --- 1. WAIT FOR THE FIRST MESSAGE ---
    deadline = time.monotonic() + args.timeout_seconds
    next_listener_check = 0.0
    poll()
    while not rows:
        now = time.monotonic()
        if now >= next_listener_check:
            if store.active_listener() is None:
                raise _listener_not_running(baseline)
            next_listener_check = now + LISTENER_CHECK_SECONDS
        if now >= deadline:
            return {
                "ok": True,
                "event": "timeout",
                "cursor": {"after_event_id": baseline},
                "timeout_seconds": args.timeout_seconds,
                "count": 0,
                "messages": [],
            }
        time.sleep(min(WAIT_POLL_SECONDS, deadline - now))
        poll()

    # --- 2. SETTLE: one request is often split over several messages ---
    # Quiet time counts from when the listener stored the newest one, so an old backlog
    # returns at once.
    window_end = time.monotonic() + args.settle_seconds * SETTLE_WINDOW_FACTOR
    while len(rows) < args.limit:
        quiet = (utc_now() - from_iso(rows[-1]["received_at"])).total_seconds()
        remaining = min(args.settle_seconds - quiet, window_end - time.monotonic())
        if remaining <= 0:
            break
        time.sleep(min(WAIT_POLL_SECONDS, remaining))
        poll()

    payload = _deliver(store, args, rows, baseline)
    payload["event"] = "message" if len(rows) == 1 else "messages"
    return payload


def _listeners(store: Store, account_id: int | None) -> dict[str, Any]:
    stale_before = utc_now() - timedelta(seconds=LISTENER_STALE_SECONDS)
    rows = [
        row for row in store.listeners() if account_id is None or row["account_id"] == account_id
    ]
    zone = display_timezone()
    listeners = [
        {
            "account_id": row["account_id"],
            "host": row["host"],
            "user": row["os_user"],
            "process_id": row["process_id"],
            "started_at": display_time(row["started_at"], zone),
            "heartbeat_at": display_time(row["heartbeat_at"], zone),
            "active": from_iso(row["heartbeat_at"]) >= stale_before,
        }
        for row in rows
    ]
    return {"ok": True, "count": len(listeners), "listeners": listeners}


def _sql(store: Store, args: argparse.Namespace) -> dict[str, Any]:
    columns, rows = store.db.read_only_query(args.query, args.max_rows)
    truncated = len(rows) > args.max_rows
    return {
        "ok": True,
        "columns": columns,
        "count": min(len(rows), args.max_rows),
        "truncated": truncated,
        "rows": [dict(zip(columns, row, strict=True)) for row in rows[: args.max_rows]],
    }


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    with Store.open() as store:
        if args.command == "info":
            nodes = [
                compact(
                    {
                        "host": row["host"],
                        "user": row["os_user"],
                        "account_id": row["account_id"],
                        "last_seen_at": display_time(row["last_seen_at"], display_timezone()),
                    }
                )
                for row in store.nodes()
            ]
            return {"ok": True, **store.info(), "nodes": nodes}
        if args.command == "sql":
            return _sql(store, args)
        if args.command == "listeners":
            return _listeners(store, args.account)
        if args.account is not None:
            store.account_id = args.account
        store.require_account()
        if args.command == "chats":
            zone = display_timezone()
            chats = [
                {
                    **peer_payload(row),
                    **compact(
                        {
                            "messages": row["messages"],
                            "last_message_date": display_time(row["last_message_date"], zone),
                            "unread_count": row["unread_count"],
                            "synced_at": display_time(row["synced_at"], zone),
                        }
                    ),
                }
                for row in store.chats(args.limit)
            ]
            return {"ok": True, "count": len(chats), "chats": chats}
        if args.command == "messages":
            return _messages(store, args)
        if args.command == "search":
            return _search(store, args)
        if args.command == "media":
            return _media(store, args)
        if args.command == "inbox":
            return _inbox(store, args)
        if args.command == "wait":
            return _wait(store, args)
    raise TeleError("invalid_command", f"Unsupported command: {args.command}", exit_code=2)


def _render_text(args: argparse.Namespace, payload: dict[str, Any]) -> str:
    if args.command in ("messages", "search", "inbox", "wait"):
        return render_messages(payload)
    if args.command == "sql":
        return render_table(payload["rows"], payload["columns"])
    if args.command == "listeners":
        return render_table(payload["listeners"])
    return render_table(payload["chats" if args.command == "chats" else "media"])


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser, argv, dispatch, _render_text)
