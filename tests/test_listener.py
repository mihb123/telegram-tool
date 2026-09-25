from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon.errors import RPCError
from telethon.sessions import SQLiteSession
from telethon.tl import types

from tele_cli.cli import local as local_cli
from tele_cli.config import Node, listener_targets
from tele_cli.errors import TeleError
from tele_cli.store.database import SQLiteDatabase
from tele_cli.store.repository import LISTENER_LEASE_EXIT_CODE, Store
from tele_cli.telegram import client, listener


def _store(tmp_path) -> Store:
    database = SQLiteDatabase(tmp_path / "messages.db")
    database.migrate()
    store = Store(database, Node("test-host", "test-user"))
    store.register_node({"id": 123, "type": "User", "username": "owner", "name": "Owner"})
    return store


def _message(message_id: int) -> dict:
    return {
        "id": message_id,
        "date": "2026-09-26T00:00:00+00:00",
        "edit_date": None,
        "sender_id": None,
        "sender": None,
        "outgoing": False,
        "text": "task",
        "reply_to_message_id": None,
        "grouped_id": None,
        "service_action": None,
        "kind": "text",
        "media": None,
        "meta": {},
    }


def test_listener_targets_parses_and_deduplicates_private_and_group_chats(monkeypatch) -> None:
    monkeypatch.setenv(
        "TELE_LISTEN_CHATS",
        " @Alice, 123456789  -100987654321,alice,@Other ",
    )
    assert listener_targets() == ("@Alice", "123456789", "-100987654321", "@Other")


def test_listener_targets_is_empty_when_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("TELE_LISTEN_CHATS", raising=False)
    assert listener_targets() == ()


def test_ipc_missing_socket_falls_back_to_direct_send(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(listener, "listener_socket_path", lambda: tmp_path / "missing.sock")
    assert asyncio.run(listener.send_via_listener({"action": "send"})) is None


def test_ipc_stale_socket_falls_back_to_direct_send(tmp_path, monkeypatch) -> None:
    path = tmp_path / "stale.sock"
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(path))
    stale.close()
    monkeypatch.setattr(listener, "listener_socket_path", lambda: path)
    assert asyncio.run(listener.send_via_listener({"action": "send"})) is None


def test_ipc_disconnect_after_request_reports_delivery_unknown(tmp_path, monkeypatch) -> None:
    path = tmp_path / "listener.sock"
    received = asyncio.Event()

    async def scenario() -> None:
        async def disconnect_after_read(reader, writer) -> None:
            assert json.loads(await reader.readline()) == {"action": "send", "text": "hello"}
            received.set()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(disconnect_after_read, path=str(path))
        try:
            with pytest.raises(TeleError) as captured:
                await listener.send_via_listener({"action": "send", "text": "hello"})
            assert captured.value.code == "delivery_unknown"
            assert captured.value.exit_code == 7
            assert "Do not automatically retry" in (captured.value.hint or "")
            assert received.is_set()
        finally:
            server.close()
            await server.wait_closed()

    monkeypatch.setattr(listener, "listener_socket_path", lambda: path)
    asyncio.run(scenario())


def test_ipc_preserves_listener_error_exit_code(tmp_path, monkeypatch) -> None:
    path = tmp_path / "listener.sock"

    async def scenario() -> None:
        async def reject(reader, writer) -> None:
            await reader.readline()
            response = listener._ipc_error(TeleError("delivery_unknown", "unknown", exit_code=7))
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(reject, path=str(path))
        try:
            with pytest.raises(TeleError) as captured:
                await listener.send_via_listener({"action": "send"})
            assert captured.value.code == "delivery_unknown"
            assert captured.value.exit_code == 7
        finally:
            server.close()
            await server.wait_closed()

    monkeypatch.setattr(listener, "listener_socket_path", lambda: path)
    asyncio.run(scenario())


def test_heartbeat_fails_after_listener_loses_lease(tmp_path) -> None:
    with _store(tmp_path) as store:
        store.claim_listener(os.getpid())
        store.release_listener(os.getpid())
        with pytest.raises(TeleError) as captured:
            store.heartbeat_listener(os.getpid())
    assert captured.value.code == "listener_lease_lost"
    assert captured.value.exit_code == LISTENER_LEASE_EXIT_CODE


def test_dead_local_listener_is_reclaimed_without_waiting_for_timeout(
    tmp_path, monkeypatch
) -> None:
    dead_pid = 987654321
    with _store(tmp_path) as store:
        store.claim_listener(dead_pid)

        def process_is_dead(process_id, signal) -> None:
            assert process_id == dead_pid
            assert signal == 0
            raise ProcessLookupError

        monkeypatch.setattr(os, "kill", process_is_dead)
        store.assert_listener_available()
        store.claim_listener(os.getpid())
        assert [row["process_id"] for row in store.listeners()] == [os.getpid()]


def test_live_listener_uses_distinct_lease_exit_code(tmp_path) -> None:
    with _store(tmp_path) as store:
        store.claim_listener(os.getpid())
        with pytest.raises(TeleError) as captured:
            store.assert_listener_available()
    assert captured.value.code == "listener_already_running"
    assert captured.value.exit_code == LISTENER_LEASE_EXIT_CODE


@pytest.mark.parametrize(
    ("error", "fatal"),
    [
        (RuntimeError("bad message payload"), False),
        (TeleError("store_error", "database unavailable"), True),
        (ConnectionError("network unavailable"), True),
    ],
)
def test_only_infrastructure_message_errors_are_fatal(tmp_path, error, fatal) -> None:
    with _store(tmp_path) as store:
        assert listener._is_infrastructure_error(error, store) is fatal


def _wait_args(*extra: str, settle: str = "0"):
    return local_cli.build_parser().parse_args(["wait", "--timeout", "1", "-s", settle, *extra])


def test_local_wait_fails_fast_without_a_listener(tmp_path) -> None:
    with _store(tmp_path) as store, pytest.raises(TeleError) as captured:
        local_cli._wait(store, _wait_args())
    assert captured.value.code == "listener_not_running"
    assert captured.value.exit_code == 12
    assert captured.value.details == {"cursor": {"after_event_id": 0}}


def test_local_wait_returns_stored_events_even_without_a_listener(tmp_path) -> None:
    chat = {"id": 10, "type": "User", "username": "alice", "name": "Alice"}
    with _store(tmp_path) as store:
        store.save_inbox_message(chat, _message(1))
        payload = local_cli._wait(store, _wait_args("--after", "0"))
    assert payload["event"] == "message"
    assert payload["cursor"] == {"after_event_id": 1}


def test_local_wait_settles_until_the_sender_stops_typing(tmp_path) -> None:
    chat = {"id": 10, "type": "User", "username": "alice", "name": "Alice"}

    def follow_up() -> None:
        time.sleep(0.5)
        with Store(SQLiteDatabase(tmp_path / "messages.db"), Node("test-host", "test-user")) as s:
            s.save_inbox_message(chat, _message(2))

    with _store(tmp_path) as store:
        store.save_inbox_message(chat, _message(1))
        writer = threading.Thread(target=follow_up)
        writer.start()
        # received_at has second precision, so leave more than a second of quiet to wait.
        payload = local_cli._wait(store, _wait_args("--after", "0", settle="2"))
        writer.join()
    assert payload["event"] == "messages"
    assert [message["id"] for message in payload["messages"]] == [1, 2]
    assert payload["cursor"] == {"after_event_id": 2}


def test_local_wait_returns_a_quiet_backlog_or_full_batch_without_settling(tmp_path) -> None:
    chat = {"id": 10, "type": "User", "username": "alice", "name": "Alice"}
    with _store(tmp_path) as store:
        store.save_inbox_message(chat, _message(1))
        store.save_inbox_message(chat, _message(2))
        started = time.monotonic()
        full = local_cli._wait(store, _wait_args("--after", "0", "-l", "1", settle="30"))
        store.db.execute("UPDATE inbox_events SET received_at = '2026-01-01T00:00:00+00:00'")
        backlog = local_cli._wait(store, _wait_args("--after", "0", settle="30"))
        elapsed = time.monotonic() - started
    assert [message["id"] for message in full["messages"]] == [1]
    assert [message["id"] for message in backlog["messages"]] == [1, 2]
    assert elapsed < 2


def test_local_wait_times_out_while_the_listener_is_healthy(tmp_path) -> None:
    with _store(tmp_path) as store:
        store.claim_listener(os.getpid())
        payload = local_cli._wait(store, _wait_args())
    assert payload["event"] == "timeout"


@pytest.mark.parametrize(
    ("listen_chats", "target"),
    [
        ("alice,-100555", "10"),  # whitelisted by username, waited on by ID
        ("10", "@Alice"),  # whitelisted by ID, waited on by username
        ("@ALICE", "alice"),
        ("", "alice"),  # no whitelist on this machine: nothing to check against
    ],
)
def test_local_wait_accepts_a_listened_chat_by_id_or_username(
    tmp_path, monkeypatch, listen_chats: str, target: str
) -> None:
    monkeypatch.setenv("TELE_LISTEN_CHATS", listen_chats)
    with _store(tmp_path) as store, pytest.raises(TeleError) as captured:
        store.save_peers([{"id": 10, "type": "User", "username": "alice", "name": "Alice"}])
        local_cli._wait(store, _wait_args("-u", target))
    assert captured.value.code == "listener_not_running"


@pytest.mark.parametrize("target", ["bob", "20"])
def test_local_wait_rejects_a_chat_outside_the_whitelist(tmp_path, monkeypatch, target) -> None:
    monkeypatch.setenv("TELE_LISTEN_CHATS", "alice,-100555")
    with _store(tmp_path) as store, pytest.raises(TeleError) as captured:
        store.save_peers([{"id": 20, "type": "User", "username": "bob", "name": "Bob"}])
        store.claim_listener(os.getpid())
        local_cli._wait(store, _wait_args("-u", target))
    assert captured.value.code == "chat_not_listened"
    assert captured.value.exit_code == 2
    assert captured.value.details["listen_chats"] == ["alice", "-100555"]


def test_session_snapshot_merges_entities_but_leaves_update_state_alone(tmp_path) -> None:
    base = tmp_path / "telegram"
    SQLiteSession(str(base)).close()

    snapshot = client._SessionSnapshot(base)
    user = types.User(id=42, access_hash=99, username="alice")
    snapshot.process_entities(types.contacts.ResolvedPeer(types.PeerUser(42), [], [user]))
    snapshot.set_update_state(
        0, types.updates.State(pts=5, qts=0, date=datetime.now(UTC), seq=1, unread_count=0)
    )
    snapshot.close()

    reread = SQLiteSession(str(base))
    assert reread.get_entity_rows_by_id(42) == (42, 99)
    assert reread.get_update_state(0) is None
    reread.close()
    assert type(client._SessionSnapshot(base).clone()) is SQLiteSession  # CDN downloads


class _FakeClient:
    """Just enough of TelegramClient for `listen_forever` to run without Telegram."""

    def __init__(self) -> None:
        self.session = SimpleNamespace(save=lambda: None)
        self.handlers: list = []
        self.stop = asyncio.Event()

    async def get_me(self):
        return types.User(id=123, access_hash=1, username="owner", first_name="Owner")

    def add_event_handler(self, callback, builder) -> None:
        self.handlers.append(callback)

    def remove_event_handler(self, callback, builder) -> None:
        self.handlers = [handler for handler in self.handlers if handler is not callback]

    async def catch_up(self) -> None:
        return None

    async def run_until_disconnected(self) -> None:
        await self.stop.wait()


class _FlakyEvent:
    """An incoming message whose chat and sender lookups both fail on Telegram's side."""

    chat_id = 10
    message = SimpleNamespace(id=5, date=datetime.now(UTC), raw_text="hello", sender_id=10)

    async def get_chat(self):
        raise RPCError(None, "FLOOD_WAIT_X")

    async def get_sender(self):
        raise ValueError("sender unavailable")


def _run_listener(tmp_path, monkeypatch, *, failing_saves: int = 0) -> tuple[dict, list[dict]]:
    """Run `listen_forever` against a fake client, deliver one `_FlakyEvent`, then stop."""
    fake = _FakeClient()
    chat = {"id": 10, "type": "User", "username": "alice", "name": "Alice"}
    node = Node("test-host", "test-user")

    @asynccontextmanager
    async def fake_client(*args, **kwargs):
        yield fake

    async def fake_resolve(client, store, target):
        store.save_peers([chat])
        return object(), chat

    def open_inbox_store() -> Store:
        inbox = Store(SQLiteDatabase(tmp_path / "messages.db"), node)
        save = inbox.save_inbox_message
        failures = iter(range(failing_saves))

        def flaky_save(*args):
            if next(failures, None) is not None:
                raise TeleError("store_error", "database briefly unavailable", exit_code=9)
            return save(*args)

        inbox.save_inbox_message = flaky_save
        return inbox

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(listener, "CAPTURE_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(listener, "authorized_client", fake_client)
    monkeypatch.setattr(listener, "resolve_chat", fake_resolve)
    monkeypatch.setattr(listener, "listener_socket_path", lambda: tmp_path / "listener.sock")
    monkeypatch.setattr(listener.Store, "open", open_inbox_store)
    emitted: list[dict] = []

    async def scenario(store: Store) -> dict:
        task = asyncio.create_task(
            listener.listen_forever(
                None, tmp_path, store, emitted.append, print_events=True, targets=("alice",)
            )
        )
        while not any(event.get("event") == "ready" for event in emitted):
            await asyncio.sleep(0.01)
        await fake.handlers[0](_FlakyEvent())
        fake.stop.set()
        return await task

    with _store(tmp_path) as store:
        try:
            result = asyncio.run(scenario(store))
        except TeleError as exc:
            result = exc.as_dict()
        rows = store.inbox_events(after_event_id=0, chat_id=None, limit=20)
        result["stored"] = [(row["event_id"], row["text"]) for row in rows]
        result["leases"] = len(store.listeners())
    return result, emitted


def test_listener_stores_message_when_chat_and_sender_lookups_fail(tmp_path, monkeypatch) -> None:
    result, emitted = _run_listener(tmp_path, monkeypatch)
    assert result["event"] == "stopped"
    assert result["stored"] == [(1, "hello")]
    assert result["leases"] == 0
    message_events = [event for event in emitted if event.get("event") == "message"]
    assert message_events[0]["chat"]["name"] == "Alice"
    assert message_events[0]["message"]["text"] == "hello"


def test_listener_retries_a_transient_store_failure(tmp_path, monkeypatch) -> None:
    result, emitted = _run_listener(tmp_path, monkeypatch, failing_saves=2)
    assert result["stored"] == [(1, "hello")]
    assert not [event for event in emitted if event.get("event") == "message_error"]


def test_listener_stops_when_the_store_keeps_failing(tmp_path, monkeypatch) -> None:
    result, emitted = _run_listener(tmp_path, monkeypatch, failing_saves=listener.CAPTURE_ATTEMPTS)
    assert result["error"]["code"] == "store_error"
    assert result["stored"] == []
    assert result["leases"] == 0
    assert [event["event"] for event in emitted if "event" in event][-1] == "message_error"


def _local_args(*argv: str):
    return local_cli.build_parser().parse_args(list(argv))


def test_inbox_and_wait_filter_by_sender_inside_a_group(tmp_path) -> None:
    group = {"id": -100200, "type": "Channel", "username": None, "name": "Team"}
    with _store(tmp_path) as store:
        store.save_peers(
            [
                {"id": 172, "type": "User", "username": "nam172", "name": "Nam"},
                {"id": 99, "type": "User", "username": "other", "name": "Other"},
            ]
        )
        store.save_inbox_message(group, {**_message(1), "sender_id": 99})
        store.save_inbox_message(group, {**_message(2), "sender_id": 172})
        inbox = local_cli._inbox(store, _local_args("inbox", "--after", "0", "--from", "nam172"))
        waited = local_cli._wait(store, _wait_args("--after", "0", "--from", "@nam172"))
    assert [message["id"] for message in inbox["messages"]] == [2]
    assert [message["id"] for message in waited["messages"]] == [2]


def test_consumer_cursor_resumes_without_after_and_can_replay(tmp_path) -> None:
    chat = {"id": 10, "type": "User", "username": "alice", "name": "Alice"}
    with _store(tmp_path) as store:
        store.save_inbox_message(chat, _message(1))  # before the consumer existed
        store.claim_listener(os.getpid())
        first = local_cli._wait(store, _wait_args("--consumer", "claude-nam172"))
        store.save_inbox_message(chat, _message(2))
        second = local_cli._wait(store, _wait_args("--consumer", "claude-nam172"))
        drained = local_cli._inbox(store, _local_args("inbox", "--consumer", "claude-nam172"))
        replay = local_cli._inbox(
            store, _local_args("inbox", "--consumer", "claude-nam172", "--after", "0")
        )
    assert first["event"] == "timeout"
    assert first["cursor"] == {"after_event_id": 1}
    assert [message["id"] for message in second["messages"]] == [2]
    assert second["consumer"] == "claude-nam172"
    assert drained["messages"] == []
    assert drained["cursor"] == {"after_event_id": 2}
    assert [message["id"] for message in replay["messages"]] == [1, 2]


@pytest.mark.parametrize("name", ["", "-leading-dash", "has space", "x" * 65])
def test_consumer_names_are_validated(name: str) -> None:
    with pytest.raises(SystemExit):
        _local_args("wait", "--consumer", name)
