from __future__ import annotations

import argparse
import json

import pytest

from tele_cli.cli import local as local_cli
from tele_cli.cli import tele as cli
from tele_cli.config import Credentials
from tele_cli.errors import TeleError


class FakeStore:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None


def _avoid_real_store(monkeypatch) -> None:
    monkeypatch.setattr(cli.Store, "open", lambda: FakeStore())


def test_requested_short_options_accept_negative_group_id() -> None:
    args = cli.build_parser().parse_args(["get", "-u", "-5539294370", "-l", "10"])
    assert args.command == "get"
    assert args.target == "-5539294370"
    assert args.limit == 10


def test_get_media_options_and_directory_imply_download() -> None:
    args = cli.build_parser().parse_args(
        [
            "get",
            "-u",
            "amacvn",
            "-d",
            "./evidence",
            "--max-media-mb",
            "250.5",
        ]
    )
    assert args.download_media is False
    assert args.download_dir == "./evidence"
    assert args.max_media_mb == 250.5


@pytest.mark.parametrize("value", ["-1", "many", "nan", "inf"])
def test_media_size_limit_must_be_non_negative(value: str) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["get", "-u", "amacvn", "--download-media", "--max-media-mb", value]
        )


def test_send_short_options_accept_negative_group_id() -> None:
    args = cli.build_parser().parse_args(
        ["send", "-u", "-5539294370", "-m", "Xin chào", "--reply-to", "42"]
    )
    assert args.target == "-5539294370"
    assert args.message == "Xin chào"
    assert args.reply_to == 42


def test_wait_accepts_cursor_timeout_and_negative_group_id() -> None:
    args = cli.build_parser().parse_args(
        ["wait", "-u", "-5539294370", "--after", "101", "--timeout", "1200"]
    )
    assert args.target == "-5539294370"
    assert args.after_message_id == 101
    assert args.timeout_seconds == 1200


@pytest.mark.parametrize("value", ["0", "86401", "1.5", "many"])
def test_wait_timeout_is_bounded(value: str) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["wait", "-u", "amacvn", "--timeout", value])


def test_send_requires_exactly_one_message_source() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["send", "-u", "amacvn"])
    with pytest.raises(SystemExit):
        parser.parse_args(["send", "-u", "amacvn", "-m", "one", "--stdin"])


@pytest.mark.parametrize("value", ["0", "-1", "5001", "many"])
def test_limit_is_bounded(value: str) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["get", "-u", "me", "-l", value])


def test_get_prints_machine_readable_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_credentials", lambda: Credentials(123, "secret"))
    _avoid_real_store(monkeypatch)

    async def fake_fetch(credentials, session, store, target, limit, **kwargs):
        assert target == "amacvn"
        assert limit == 10
        assert kwargs["download_media"] is False
        assert kwargs["download_dir"] is None
        assert kwargs["max_media_bytes"] == 100 * 1024 * 1024
        assert kwargs["refresh"] is False
        assert kwargs["meta"] is False
        return {
            "ok": True,
            "query": {"target": target, "limit": limit, "order": "newest_first"},
            "chat": {"id": 7, "type": "User", "username": target, "name": "AMAC"},
            "count": 1,
            "messages": [
                {
                    "id": 42,
                    "date": "2026-09-25T08:00:00+00:00",
                    "sender": {"id": 7, "username": target, "name": "AMAC"},
                    "text": "Task mới",
                }
            ],
        }

    monkeypatch.setattr(cli.history, "get_messages", fake_fetch)
    assert cli.main(["get", "-u", "amacvn", "-l", "10"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["messages"][0]["text"] == "Task mới"


def test_get_dispatches_media_download_options(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setattr(cli, "load_credentials", lambda: Credentials(123, "secret"))
    _avoid_real_store(monkeypatch)

    async def fake_fetch(credentials, session, store, target, limit, **kwargs):
        assert kwargs["download_media"] is True
        assert kwargs["download_dir"] == tmp_path
        assert kwargs["max_media_bytes"] == 25 * 1024 * 1024
        return {
            "ok": True,
            "chat": {"id": 7},
            "count": 0,
            "messages": [],
            "media_directory": str(tmp_path),
        }

    monkeypatch.setattr(cli.history, "get_messages", fake_fetch)
    result = cli.main(
        [
            "get",
            "-u",
            "amacvn",
            "-d",
            str(tmp_path),
            "--max-media-mb",
            "25",
        ]
    )
    assert result == 0
    assert json.loads(capsys.readouterr().out)["media_directory"] == str(tmp_path)


def test_errors_are_json_on_stderr(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_credentials", lambda: Credentials(123, "secret"))
    _avoid_real_store(monkeypatch)

    async def fail(*args, **kwargs):
        raise TeleError("peer_not_found", "No peer", exit_code=5)

    monkeypatch.setattr(cli.history, "get_messages", fail)
    assert cli.main(["get", "-u", "missing"]) == 5
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["error"]["code"] == "peer_not_found"


def test_send_dry_run_dispatches_exact_text(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_credentials", lambda: Credentials(123, "secret"))
    _avoid_real_store(monkeypatch)

    async def fake_send(args, credentials, session, store, text):
        assert args.target == "amacvn"
        assert text == "Mình cần hỏi thêm"
        assert args.reply_to == 42
        assert args.link_preview is False
        assert args.dry_run is True
        return {
            "ok": True,
            "action": "dry_run",
            "chat": {"id": 7, "username": "amacvn", "name": "AMAC"},
            "request": {"text": text},
        }

    monkeypatch.setattr(cli, "_send_text", fake_send)
    result = cli.main(
        [
            "send",
            "-u",
            "amacvn",
            "-m",
            "Mình cần hỏi thêm",
            "--reply-to",
            "42",
            "--dry-run",
        ]
    )
    assert result == 0
    assert json.loads(capsys.readouterr().out)["action"] == "dry_run"


def test_wait_dispatches_cursor_and_timeout(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_credentials", lambda: Credentials(123, "secret"))
    _avoid_real_store(monkeypatch)

    async def fake_wait(credentials, session, store, target, after_message_id, timeout_seconds):
        assert target == "amacvn"
        assert after_message_id == 101
        assert timeout_seconds == 60
        return {
            "ok": True,
            "event": "timeout",
            "chat": {"id": 7, "username": "amacvn"},
            "cursor": {"after_message_id": 101},
            "timeout_seconds": 60,
        }

    monkeypatch.setattr(cli.messaging, "wait_for_message", fake_wait)
    assert cli.main(["wait", "-u", "amacvn", "--after", "101", "--timeout", "60"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "timeout"
    assert payload["cursor"]["after_message_id"] == 101


def test_send_reads_utf8_file_and_rejects_empty(tmp_path) -> None:
    draft = tmp_path / "draft.txt"
    draft.write_text("Dòng một\nDòng hai\n", encoding="utf-8")
    args = cli.build_parser().parse_args(["send", "-u", "amacvn", "--file", str(draft)])
    assert cli._message_text(args) == "Dòng một\nDòng hai"

    draft.write_text(" \n", encoding="utf-8")
    with pytest.raises(TeleError, match="empty"):
        cli._message_text(args)


def test_send_rejects_oversized_message() -> None:
    args = cli.build_parser().parse_args(["send", "-u", "amacvn", "-m", "x" * 4097])
    with pytest.raises(TeleError, match="4096"):
        cli._message_text(args)


def test_help_messages_contain_limit_and_format_explanations() -> None:
    local_subparsers = local_cli.build_parser()._actions[-1].choices

    wait_actions = {a.dest: a for a in local_subparsers["wait"]._actions}
    assert wait_actions["limit"].help == "Maximum number of events to return (default: 100)"
    assert wait_actions["format"].help == "Output format (default: json)"

    chats_actions = {a.dest: a for a in local_subparsers["chats"]._actions}
    assert chats_actions["limit"].help == "Maximum number of chats to list (default: 50)"

    messages_actions = {a.dest: a for a in local_subparsers["messages"]._actions}
    assert messages_actions["limit"].help == "Maximum number of messages to return (default: 20)"

    search_actions = {a.dest: a for a in local_subparsers["search"]._actions}
    assert search_actions["limit"].help == "Maximum number of messages to return (default: 20)"

    media_actions = {a.dest: a for a in local_subparsers["media"]._actions}
    assert media_actions["limit"].help == "Maximum number of media items to return (default: 50)"

    inbox_actions = {a.dest: a for a in local_subparsers["inbox"]._actions}
    assert inbox_actions["limit"].help == "Maximum number of messages to return (default: 20)"

    sql_actions = {a.dest: a for a in local_subparsers["sql"]._actions}
    assert sql_actions["max_rows"].help == "Maximum number of rows to return (default: 200)"

    tele_subparsers = cli.build_parser()._actions[-1].choices
    get_actions = {a.dest: a for a in tele_subparsers["get"]._actions}
    expected_get_limit_help = "Maximum number of messages to fetch (default: 10; maximum: 5000)"
    assert get_actions["limit"].help == expected_get_limit_help
    assert get_actions["format"].help == "Output format (default: json)"

    dialogs_actions = {a.dest: a for a in tele_subparsers["dialogs"]._actions}
    expected_dialogs_limit_help = "Maximum number of dialogs to list (default: 50; maximum: 100)"
    assert dialogs_actions["limit"].help == expected_dialogs_limit_help


def _all_parsers(parser):
    yield parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                yield from _all_parsers(subparser)


@pytest.mark.parametrize("build_parser", [cli.build_parser, local_cli.build_parser])
def test_every_option_has_a_short_unless_both_candidates_are_taken(build_parser) -> None:
    for parser in _all_parsers(build_parser()):
        taken = parser._option_string_actions
        for action in parser._actions:
            if not action.option_strings or any(
                not option.startswith("--") for option in action.option_strings
            ):
                continue
            name = action.option_strings[0].lstrip("-").replace("-", "")
            assert f"-{name[:1]}" in taken and f"-{name[:2]}" in taken, action.option_strings


def test_short_flags_follow_first_letter_then_first_two_letters() -> None:
    args = local_cli.build_parser().parse_args(
        ["messages", "-u", "-100123", "-ar", "5", "-s", "2d", "-un", "1d", "-f", "bob", "-ol"]
        + ["-m", "-fo", "text"]
    )
    assert (args.target, args.around, args.sender, args.oldest_first) == ("-100123", 5, "bob", True)
    assert (args.meta, args.format) == (True, "text")
    args = local_cli.build_parser().parse_args(["wait", "-t", "60", "-a", "7", "-c", "agent"])
    assert (args.timeout_seconds, args.after_event_id, args.consumer) == (60, 7, "agent")
    args = cli.build_parser().parse_args(["send", "-u", "me", "-f", "msg.txt", "-r", "3", "-d"])
    assert (args.file, args.reply_to, args.dry_run) == ("msg.txt", 3, True)
    get_options = {
        a.dest: a.option_strings for a in cli.build_parser()._actions[-1].choices["get"]._actions
    }
    assert get_options["max_age"] == ["--max-age"]  # -m (--meta) and -ma (--max-media-mb) taken
