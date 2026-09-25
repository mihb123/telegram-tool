"""`tele`: read, download media, send and wait on Telegram, caching everything locally."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import (
    ConfigError,
    load_credentials,
    save_credentials,
    session_path,
    validate_credentials,
)
from ..errors import TeleError
from ..output import render_messages, render_table
from ..store import Store
from ..telegram import client, dialogs, history, messaging
from .args import (
    add_format,
    add_target,
    bounded_int,
    non_negative_megabytes,
    positive_message_id,
)
from .runner import run

DEFAULT_CACHE_MAX_AGE = 300


def _default_max_age() -> int:
    try:
        return max(0, int(os.environ.get("TELE_CACHE_MAX_AGE", DEFAULT_CACHE_MAX_AGE)))
    except ValueError:
        return DEFAULT_CACHE_MAX_AGE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tele",
        description="Read and send messages with your authorized Telegram account.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser("configure", help="Save Telegram API credentials")
    configure.add_argument("--api-id", help="Telegram API ID (prompted when omitted)")
    configure.add_argument(
        "--api-hash",
        help="Telegram API hash (prefer the hidden prompt to avoid shell history)",
    )

    auth = subparsers.add_parser("auth", help="Interactively authorize the Telegram account")
    auth.add_argument("--phone", help="Phone number including country code (prompted when omitted)")

    subparsers.add_parser("status", help="Check whether the local session is authorized")

    get = subparsers.add_parser(
        "get",
        help="Get recent messages, newest first (served from the local cache when possible)",
    )
    add_target(get)
    get.add_argument("-l", "--limit", type=bounded_int("limit", 1, 100), default=10)
    add_format(get)
    get.add_argument(
        "--download-media",
        action="store_true",
        help="Download message photos and other media (files already on disk are reused)",
    )
    get.add_argument(
        "-d",
        "--download-dir",
        help="Media output directory; also enables --download-media",
    )
    get.add_argument(
        "--max-media-mb",
        type=non_negative_megabytes,
        default=100.0,
        help="Maximum size per media file in MB; 0 disables the limit (default: 100)",
    )
    get.add_argument(
        "--max-age",
        type=bounded_int("max age", 0, 31_536_000),
        default=_default_max_age(),
        help=(
            "Answer from the local cache without contacting Telegram when the chat was "
            "checked within this many seconds; 0 always checks for new messages "
            f"(default: $TELE_CACHE_MAX_AGE or {DEFAULT_CACHE_MAX_AGE})"
        ),
    )
    get.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch the whole window from Telegram to pick up edits and deletions",
    )

    send = subparsers.add_parser("send", help="Send a plain-text message")
    add_target(send)
    message_source = send.add_mutually_exclusive_group(required=True)
    message_source.add_argument("-m", "--message", help="Plain-text message")
    message_source.add_argument(
        "--stdin",
        action="store_true",
        help="Read the entire plain-text message from standard input",
    )
    message_source.add_argument(
        "--file",
        type=str,
        help="Read the plain-text message from a UTF-8 file",
    )
    send.add_argument("--reply-to", type=positive_message_id, help="Reply to this message ID")
    send.add_argument(
        "--link-preview",
        action="store_true",
        help="Enable Telegram link previews (disabled by default)",
    )
    send.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and show the destination without sending",
    )

    wait = subparsers.add_parser(
        "wait",
        help="Wait for a new incoming message in a conversation",
    )
    add_target(wait)
    wait.add_argument(
        "--after",
        dest="after_message_id",
        type=positive_message_id,
        help="Return the first incoming message newer than this message ID",
    )
    wait.add_argument(
        "--timeout",
        dest="timeout_seconds",
        type=bounded_int("timeout", 1, 86_400),
        default=900,
        help="Maximum wait in seconds (default: 900; maximum: 86400)",
    )

    dialogs = subparsers.add_parser("dialogs", help="List recent dialogs and their IDs")
    dialogs.add_argument("-l", "--limit", type=bounded_int("limit", 1, 100), default=50)
    add_format(dialogs)
    return parser


def _credentials_or_error():
    try:
        return load_credentials()
    except ConfigError as exc:
        raise TeleError(
            "configuration_error",
            str(exc),
            exit_code=3,
            hint="Run `tele configure` in an interactive terminal.",
        ) from exc


def _message_text(args: argparse.Namespace) -> str:
    if args.message is not None:
        text = args.message
    elif args.stdin:
        text = sys.stdin.read()
    else:
        try:
            text = Path(args.file).expanduser().read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise TeleError(
                "message_file_error",
                f"Cannot read UTF-8 message file {args.file!r}: {exc}",
                exit_code=2,
            ) from exc

    if args.message is None:
        text = text.rstrip("\r\n")
    if not text.strip():
        raise TeleError("invalid_message", "Message cannot be empty.", exit_code=2)
    if len(text) > 4096:
        raise TeleError(
            "invalid_message",
            f"Message has {len(text)} characters; Telegram text messages allow at most 4096.",
            exit_code=2,
        )
    return text


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "configure":
        api_id = args.api_id or input("Telegram API ID: ").strip()
        api_hash = args.api_hash or getpass.getpass("Telegram API hash: ").strip()
        try:
            credentials = validate_credentials(api_id, api_hash)
        except ConfigError as exc:
            raise TeleError("configuration_error", str(exc), exit_code=3) from exc
        destination = save_credentials(credentials)
        return {"ok": True, "configured": True, "config_path": str(destination)}

    credentials = _credentials_or_error()
    session = session_path()
    with Store.open() as store:
        if args.command == "auth":
            if not sys.stdin.isatty():
                raise TeleError(
                    "interactive_terminal_required",
                    "Telegram authorization requires an interactive terminal.",
                    exit_code=6,
                    hint="Run `tele auth` yourself in a terminal, then let the agent retry.",
                )
            return asyncio.run(client.authorize(credentials, session, store, args.phone))
        if args.command == "status":
            return asyncio.run(client.status(credentials, session, store))
        if args.command == "get":
            return asyncio.run(
                history.get_messages(
                    credentials,
                    session,
                    store,
                    args.target,
                    args.limit,
                    download_media=args.download_media or args.download_dir is not None,
                    download_dir=Path(args.download_dir).expanduser()
                    if args.download_dir
                    else None,
                    max_media_bytes=int(args.max_media_mb * 1024 * 1024),
                    max_age=args.max_age,
                    refresh=args.refresh,
                )
            )
        if args.command == "send":
            return asyncio.run(
                messaging.send_text(
                    credentials,
                    session,
                    store,
                    args.target,
                    _message_text(args),
                    args.reply_to,
                    args.link_preview,
                    args.dry_run,
                )
            )
        if args.command == "wait":
            return asyncio.run(
                messaging.wait_for_message(
                    credentials,
                    session,
                    store,
                    args.target,
                    args.after_message_id,
                    args.timeout_seconds,
                )
            )
        if args.command == "dialogs":
            return asyncio.run(dialogs.fetch_dialogs(credentials, session, store, args.limit))
    raise TeleError("invalid_command", f"Unsupported command: {args.command}", exit_code=2)


def _render_text(args: argparse.Namespace, payload: dict[str, Any]) -> str:
    if args.command == "get":
        return render_messages(payload)
    return render_table(
        payload["dialogs"], ["id", "name", "username", "type", "unread_count", "last_message_date"]
    )


def main(argv: Sequence[str] | None = None) -> int:
    return run(build_parser, argv, dispatch, _render_text)
