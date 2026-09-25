from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import math
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .config import (
    ConfigError,
    load_credentials,
    save_credentials,
    session_path,
    validate_credentials,
)
from .errors import TeleError
from .telegram_api import (
    authorize,
    fetch_dialogs,
    fetch_messages,
    send_text,
    status,
    wait_for_message,
)


def positive_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc
    if not 1 <= limit <= 100:
        raise argparse.ArgumentTypeError("limit must be between 1 and 100")
    return limit


def positive_message_id(value: str) -> int:
    try:
        message_id = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("message ID must be an integer") from exc
    if message_id <= 0:
        raise argparse.ArgumentTypeError("message ID must be positive")
    return message_id


def wait_timeout(value: str) -> int:
    try:
        timeout = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be an integer") from exc
    if not 1 <= timeout <= 86_400:
        raise argparse.ArgumentTypeError("timeout must be between 1 and 86400 seconds")
    return timeout


def non_negative_megabytes(value: str) -> float:
    try:
        megabytes = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("media size limit must be a number") from exc
    if not math.isfinite(megabytes) or megabytes < 0:
        raise argparse.ArgumentTypeError("media size limit must be a finite non-negative number")
    return megabytes


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

    get = subparsers.add_parser("get", help="Get recent messages, newest first")
    get.add_argument(
        "-u",
        "--user",
        "--chat",
        dest="target",
        required=True,
        help="Username, @username, numeric group/channel ID, or 'me'",
    )
    get.add_argument("-l", "--limit", type=positive_limit, default=10)
    get.add_argument("--format", choices=("json", "text"), default="json")
    get.add_argument(
        "--download-media",
        action="store_true",
        help="Download message photos and other media",
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

    send = subparsers.add_parser("send", help="Send a plain-text message")
    send.add_argument(
        "-u",
        "--user",
        "--chat",
        dest="target",
        required=True,
        help="Username, @username, numeric group/channel ID, or 'me'",
    )
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
    wait.add_argument(
        "-u",
        "--user",
        "--chat",
        dest="target",
        required=True,
        help="Username, @username, numeric group/channel ID, or 'me'",
    )
    wait.add_argument(
        "--after",
        dest="after_message_id",
        type=positive_message_id,
        help="Return the first incoming message newer than this message ID",
    )
    wait.add_argument(
        "--timeout",
        dest="timeout_seconds",
        type=wait_timeout,
        default=900,
        help="Maximum wait in seconds (default: 900; maximum: 86400)",
    )

    dialogs = subparsers.add_parser("dialogs", help="List recent dialogs and their IDs")
    dialogs.add_argument("-l", "--limit", type=positive_limit, default=50)
    dialogs.add_argument("--format", choices=("json", "text"), default="json")
    return parser


def _json_dump(payload: dict[str, Any], stream: Any = None) -> None:
    stream = stream or sys.stdout
    json.dump(payload, stream, ensure_ascii=False, indent=2)
    stream.write("\n")


def _text_messages(payload: dict[str, Any]) -> str:
    chat = payload["chat"]
    lines = [f"Chat: {chat.get('name') or chat.get('username') or chat['id']} ({chat['id']})"]
    for message in payload["messages"]:
        sender = message["sender"]
        sender_label = sender.get("name") or sender.get("username") or sender.get("id") or "unknown"
        fallback = message.get("media_type") or message.get("service_action") or "empty"
        text = message["text"] or f"[{fallback}]"
        lines.extend(
            [
                "",
                f"[{message['date']}] #{message['id']} {sender_label}",
                text,
            ]
        )
        media = message.get("media") or {}
        if media.get("local_path"):
            lines.append(f"Media: {media['local_path']}")
        elif media.get("download_status"):
            lines.append(f"Media: {media['download_status']}")
    return "\n".join(lines)


def _text_dialogs(payload: dict[str, Any]) -> str:
    return "\n".join(
        f"{item['id']}\t{item['name']}\t@{item['username'] or '-'}\t{item['type']}"
        for item in payload["dialogs"]
    )


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
    if args.command == "auth":
        if not sys.stdin.isatty():
            raise TeleError(
                "interactive_terminal_required",
                "Telegram authorization requires an interactive terminal.",
                exit_code=6,
                hint="Run `tele auth` yourself in a terminal, then let the agent retry.",
            )
        return asyncio.run(authorize(credentials, session, args.phone))
    if args.command == "status":
        return asyncio.run(status(credentials, session))
    if args.command == "get":
        download_media = args.download_media or args.download_dir is not None
        download_dir = Path(args.download_dir).expanduser() if args.download_dir else None
        max_media_bytes = int(args.max_media_mb * 1024 * 1024)
        return asyncio.run(
            fetch_messages(
                credentials,
                session,
                args.target,
                args.limit,
                download_media=download_media,
                download_dir=download_dir,
                max_media_bytes=max_media_bytes,
            )
        )
    if args.command == "send":
        return asyncio.run(
            send_text(
                credentials,
                session,
                args.target,
                _message_text(args),
                args.reply_to,
                args.link_preview,
                args.dry_run,
            )
        )
    if args.command == "wait":
        return asyncio.run(
            wait_for_message(
                credentials,
                session,
                args.target,
                args.after_message_id,
                args.timeout_seconds,
            )
        )
    if args.command == "dialogs":
        return asyncio.run(fetch_dialogs(credentials, session, args.limit))
    raise TeleError("invalid_command", f"Unsupported command: {args.command}", exit_code=2)


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = dispatch(args)
        if getattr(args, "format", "json") == "text":
            rendered = _text_messages(payload) if args.command == "get" else _text_dialogs(payload)
            print(rendered)
        else:
            _json_dump(payload)
        return 0
    except TeleError as exc:
        _json_dump(exc.as_dict(), stream=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        _json_dump(
            {"ok": False, "error": {"code": "interrupted", "message": "Operation cancelled."}},
            stream=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
