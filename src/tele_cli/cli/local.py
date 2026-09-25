"""`tele-local`: query messages already fetched by `tele`, without contacting Telegram.

Reads the same database as `tele` (local SQLite, or the shared PostgreSQL server from
TELE_DATABASE_URL) and never imports Telethon.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Any

from .. import __version__
from ..errors import TeleError
from ..output import render_messages, render_table
from ..store import MessageQuery, Store
from ..store.database import fold
from ..store.payloads import compact, media_payload, messages_payload, peer_payload
from ..values import parse_target
from .args import add_format, add_target, bounded_int, positive_message_id, time_bound
from .runner import run

LIMIT = bounded_int("limit", 1, 1000)


def _add_time_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--since", type=time_bound(end_of_day=False), help="From this time: 2h, 3d, 2026-09-25"
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
    chats.add_argument("-l", "--limit", type=LIMIT, default=50)
    add_format(chats)

    messages = subparsers.add_parser("messages", help="Messages of one chat, newest first")
    add_target(messages)
    messages.add_argument("-l", "--limit", type=LIMIT, default=20)
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
    add_format(messages)

    search = subparsers.add_parser(
        "search",
        help="Find messages by text or file name (case- and accent-insensitive)",
    )
    search.add_argument("query", help="Words that must all appear, e.g. 'doi soat'")
    add_target(search, required=False)
    search.add_argument("-l", "--limit", type=LIMIT, default=20)
    _add_time_filters(search)
    add_format(search)

    media = subparsers.add_parser(
        "media", help="Attachments and which machines (host, user) hold their files"
    )
    add_target(media, required=False)
    media.add_argument("-l", "--limit", type=LIMIT, default=50)
    media.add_argument("--type", dest="media_type", help="Photo, Document, WebPage, ...")
    state = media.add_mutually_exclusive_group()
    state.add_argument(
        "--downloaded", action="store_true", help="Only media with a file on this machine"
    )
    state.add_argument("--missing", action="store_true", help="Only media not on this machine")
    add_format(media)

    sql = subparsers.add_parser(
        "sql",
        help="Run a read-only SQL query; fold(text) strips case and accents",
    )
    sql.add_argument("query", help='e.g. "SELECT id, text FROM messages WHERE chat_id = -123"')
    sql.add_argument("--max-rows", type=LIMIT, default=200)
    add_format(sql)

    subparsers.add_parser(
        "info", help="Database, schema version, row counts and machines using the database"
    )
    return parser


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
        "synced_at": state["checked_at"] if state else None,
        "order": "oldest_first" if args.oldest_first else "newest_first",
        "count": len(rows),
        "messages": messages_payload(store, rows),
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
        "messages": messages_payload(store, rows, include_chat=chat is None),
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
    items = [
        {
            **compact({"chat_id": row["chat_id"], "chat_name": row["chat_name"]}),
            "message_id": row["message_id"],
            "date": row["date"],
            **media_payload(row, files.get((row["chat_id"], row["message_id"]), []), store.node),
        }
        for row in rows
    ]
    return {"ok": True, "count": len(items), "media": items}


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
                        "last_seen_at": row["last_seen_at"],
                    }
                )
                for row in store.nodes()
            ]
            return {"ok": True, **store.info(), "nodes": nodes}
        if args.command == "sql":
            return _sql(store, args)
        if args.account is not None:
            store.account_id = args.account
        store.require_account()
        if args.command == "chats":
            chats = [
                {
                    **peer_payload(row),
                    **compact(
                        {
                            key: row[key]
                            for key in (
                                "messages",
                                "last_message_date",
                                "unread_count",
                                "synced_at",
                            )
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
    raise TeleError("invalid_command", f"Unsupported command: {args.command}", exit_code=2)


def _render_text(args: argparse.Namespace, payload: dict[str, Any]) -> str:
    if args.command in ("messages", "search"):
        return render_messages(payload)
    if args.command == "sql":
        return render_table(payload["rows"], payload["columns"])
    return render_table(payload["chats" if args.command == "chats" else "media"])


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser, argv, dispatch, _render_text)
