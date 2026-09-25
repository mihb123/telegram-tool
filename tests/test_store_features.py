from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tele_cli.config import Node, display_timezone
from tele_cli.output import render_messages
from tele_cli.store.database import SQLiteDatabase
from tele_cli.store.payloads import compact, message_payload
from tele_cli.store.repository import Store
from tele_cli.telegram.history import _report_download
from tele_cli.telegram.records import media_kind
from tele_cli.values import parse_time_bound


def _store(tmp_path) -> Store:
    database = SQLiteDatabase(tmp_path / "messages.db")
    database.migrate()
    store = Store(database, Node("test-host", "test-user"))
    store.register_node({"id": 123, "type": "User", "username": "owner", "name": "Owner"})
    return store


def _message(message_id: int, text: str = "task") -> dict:
    return {
        "id": message_id,
        "date": f"2026-09-26T00:00:{message_id:02d}+00:00",
        "edit_date": None,
        "sender_id": None,
        "sender": None,
        "outgoing": False,
        "text": text,
        "reply_to_message_id": None,
        "grouped_id": None,
        "service_action": None,
        "media": None,
        "meta": {},
    }


def test_inbox_cursor_is_account_wide_ordered_and_deduplicated(tmp_path) -> None:
    first_chat = {"id": 10, "type": "User", "username": "alice", "name": "Alice"}
    second_chat = {"id": -10020, "type": "Channel", "username": None, "name": "Team"}
    with _store(tmp_path) as store:
        assert store.save_inbox_message(first_chat, _message(1)) == 1
        assert store.save_inbox_message(first_chat, _message(1)) is None
        assert store.save_inbox_message(second_chat, _message(2)) == 2
        assert store.latest_inbox_event_id() == 2
        rows = store.inbox_events(after_event_id=1, chat_id=None, limit=20)
        assert [(row["event_id"], row["chat_id"], row["id"]) for row in rows] == [(2, -10020, 2)]


def test_compact_payload_keeps_meaningful_false_and_zero_values() -> None:
    assert compact({"none": None, "empty": "", "false": False, "zero": 0, "text": "x"}) == {
        "false": False,
        "zero": 0,
        "text": "x",
    }


def test_short_message_payload_contains_only_agent_facing_fields() -> None:
    row = {
        "id": 7,
        "date": "2026-09-26T01:02:03+00:00",
        "edit_date": None,
        "chat_id": 10,
        "chat_name": "Alice",
        "chat_username": "alice",
        "sender_id": 10,
        "sender_name": "Alice",
        "sender_username": "alice",
        "media_kind": None,
        "media_type": None,
        "service_action": None,
        "text": "Help",
        "meta": None,
    }
    payload = message_payload(row, [], Node("host", "user"), UTC)
    assert payload == {
        "id": 7,
        "time": "2026-09-26 01:02:03",
        "sender": "Alice",
        "type": "text",
        "text": "Help",
    }


@pytest.mark.parametrize(
    ("message", "media_type", "expected"),
    [
        (SimpleNamespace(sticker=True), "Document", "sticker"),
        (SimpleNamespace(voice=True), "Document", "voice"),
        (SimpleNamespace(document=True), "Document", "file"),
        (SimpleNamespace(photo=True), "WebPage", None),
    ],
)
def test_media_kind_prefers_specific_telegram_semantics(message, media_type, expected) -> None:
    assert media_kind(message, media_type) == expected


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("+7", timedelta(hours=7)), ("UTC-05:30", -timedelta(hours=5, minutes=30))],
)
def test_display_timezone_accepts_offsets(monkeypatch, configured, expected) -> None:
    monkeypatch.setenv("TELE_TIMEZONE", configured)
    assert display_timezone().utcoffset(datetime.now(UTC)) == expected


@pytest.mark.parametrize("configured", ["UTC+24", "+07:60", "not/a-zone"])
def test_display_timezone_falls_back_for_invalid_values(monkeypatch, configured) -> None:
    monkeypatch.setenv("TELE_TIMEZONE", configured)
    assert display_timezone().utcoffset(datetime.now(UTC)) == timedelta(hours=7)


def test_inbox_cursor_is_never_reused_after_its_message_is_deleted(tmp_path) -> None:
    chat = {"id": 10, "type": "User", "username": "alice", "name": "Alice"}
    with _store(tmp_path) as store:
        assert store.save_inbox_message(chat, _message(1)) == 1
        assert store.save_inbox_message(chat, _message(2)) == 2
        store.delete_messages_except(chat["id"], 2, 2, [])  # `tele get` saw it deleted
        assert store.inbox_events(after_event_id=1, chat_id=None, limit=20) == []
        assert store.latest_inbox_event_id() == 2
        assert store.save_inbox_message(chat, _message(3)) == 3


def test_naive_time_bounds_use_the_display_timezone() -> None:
    zone = timezone(timedelta(hours=7))
    assert parse_time_bound("2026-09-26 10:00:00", zone) == "2026-09-26T03:00:00+00:00"
    assert parse_time_bound("2026-09-26", zone, end_of_day=True) == "2026-09-26T17:00:00+00:00"
    assert parse_time_bound("2026-09-26T10:00:00+00:00", zone) == "2026-09-26T10:00:00+00:00"


def _media_row(**fields) -> dict:
    row = {
        "id": 8,
        "date": "2026-09-26T01:02:03+00:00",
        "edit_date": None,
        "chat_id": 10,
        "chat_name": "Alice",
        "chat_username": "alice",
        "sender_id": 10,
        "sender_name": "Alice",
        "sender_username": "alice",
        "service_action": None,
        "outgoing": 0,
        "reply_to_message_id": None,
        "grouped_id": None,
        "text": "",
        "meta": None,
        "media_type": "Document",
        "media_kind": "video",
        "media_name": "clip.mp4",
        "media_mime_type": "video/mp4",
        "media_size": 20_000_000,
        "media_width": None,
        "media_height": None,
        "media_duration": None,
        "media_download_status": None,
        "media_download_error": None,
    }
    return {**row, **fields}


def test_short_payload_reports_why_media_has_no_file(tmp_path) -> None:
    node = Node("host", "user")
    skipped = message_payload(_media_row(media_download_status="skipped_too_large"), [], node, UTC)
    assert skipped["download_status"] == "skipped_too_large"
    assert "path" not in skipped

    failed = _media_row(media_download_status="error", media_download_error="timeout")
    assert message_payload(failed, [], node, UTC)["download_error"] == "timeout"

    gone = [{"host": "host", "os_user": "user", "path": str(tmp_path / "deleted.mp4")}]
    assert message_payload(_media_row(), gone, node, UTC)["download_status"] == "file_missing"

    kept = tmp_path / "clip.mp4"
    kept.write_bytes(b"x")
    files = [{"host": "host", "os_user": "user", "path": str(kept)}]
    payload = message_payload(_media_row(media_download_status="error"), files, node, UTC)
    assert payload["path"] == str(kept)
    assert "download_status" not in payload


def test_get_reports_this_runs_size_limit_in_short_output() -> None:
    downloader = SimpleNamespace(statuses={1: "skipped_too_large", 2: "downloaded"}, max_bytes=5)
    skipped = {"id": 1, "type": "video"}
    downloaded = {"id": 2, "type": "photo", "path": "/files/2.jpg", "download_status": "error"}
    _report_download(skipped, downloader)
    _report_download(downloaded, downloader)
    assert skipped == {
        "id": 1,
        "type": "video",
        "download_status": "skipped_too_large",
        "max_size_bytes": 5,
    }
    assert "download_status" not in downloaded


def test_text_rendering_names_the_service_action() -> None:
    message = {"id": 5, "time": "2026-09-26 08:00:00", "type": "service", "action": "pin_message"}
    assert render_messages({"messages": [message]}).splitlines()[-1] == "[pin_message]"


def test_forward_date_is_printed_in_the_display_timezone() -> None:
    row = {
        **_media_row(media_type=None, media_kind=None),
        "meta": json.dumps({"forward": {"from_name": "Bob", "date": "2026-09-26T01:00:00+00:00"}}),
    }
    zone = timezone(timedelta(hours=7))
    payload = message_payload(row, [], Node("host", "user"), zone, meta=True)
    assert payload["forward"] == {"from_name": "Bob", "date": "2026-09-26 08:00:00"}
