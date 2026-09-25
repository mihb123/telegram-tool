"""Versioned schema of the message store, valid for both SQLite and PostgreSQL.

``schema_migrations`` records which entries of ``MIGRATIONS`` have run. Never edit a
migration that has already run on someone's database: append a new entry (``ALTER TABLE``,
``CREATE INDEX``, backfills, ...) and every machine applies it on its next start.
Stick to SQL both dialects accept: BIGINT/INTEGER/TEXT/DOUBLE PRECISION, ON CONFLICT, no
AUTOINCREMENT/SERIAL, no WITHOUT ROWID.

Message IDs are only unique per Telegram account and chat, so everything fetched is keyed
by ``account_id``: machines logged in to different accounts can share one database.
"""

from __future__ import annotations

MIGRATIONS: tuple[str, ...] = (
    # 1 — initial schema
    """
    -- Machines (hostname + OS user) using this database and their Telegram account.
    CREATE TABLE nodes (
        host          TEXT   NOT NULL,
        os_user       TEXT   NOT NULL,
        account_id    BIGINT,                 -- peers.id of the account logged in there
        last_seen_at  TEXT   NOT NULL,
        PRIMARY KEY (host, os_user)
    );

    -- Users, groups and channels: chats and message senders, shared by all accounts.
    CREATE TABLE peers (
        id          BIGINT PRIMARY KEY,       -- marked peer ID: users > 0, groups/channels < 0
        type        TEXT   NOT NULL,          -- Telethon entity class: User, Chat, Channel
        username    TEXT,
        name        TEXT,
        updated_at  TEXT   NOT NULL
    );
    CREATE INDEX peers_username ON peers (lower(username));

    -- Latest dialog list snapshot per account (`tele dialogs`).
    CREATE TABLE dialogs (
        account_id         BIGINT  NOT NULL,
        peer_id            BIGINT  NOT NULL REFERENCES peers (id) ON DELETE CASCADE,
        unread_count       INTEGER,
        last_message_date  TEXT,
        updated_at         TEXT    NOT NULL,
        PRIMARY KEY (account_id, peer_id)
    );

    CREATE TABLE messages (
        account_id           BIGINT  NOT NULL,
        chat_id              BIGINT  NOT NULL REFERENCES peers (id) ON DELETE CASCADE,
        id                   BIGINT  NOT NULL,
        date                 TEXT    NOT NULL,           -- ISO-8601, UTC
        edit_date            TEXT,
        sender_id            BIGINT,                     -- peers.id when the sender is known
        outgoing             INTEGER NOT NULL DEFAULT 0,
        text                 TEXT    NOT NULL DEFAULT '',
        reply_to_message_id  BIGINT,
        grouped_id           BIGINT,                     -- album shared by media sent together
        service_action       TEXT,                       -- MessageAction class of service messages
        fetched_at           TEXT    NOT NULL,
        PRIMARY KEY (account_id, chat_id, id)
    );
    CREATE INDEX messages_chat_date ON messages (account_id, chat_id, date);
    CREATE INDEX messages_sender ON messages (account_id, sender_id);

    -- Attachment metadata; Telegram allows at most one per message.
    CREATE TABLE media (
        account_id       BIGINT  NOT NULL,
        chat_id          BIGINT  NOT NULL,
        message_id       BIGINT  NOT NULL,
        type             TEXT    NOT NULL,       -- Photo, Document, WebPage, Poll, ...
        file_key         TEXT,                   -- "photo:<id>" or "document:<id>"
        name             TEXT,
        extension        TEXT,
        mime_type        TEXT,
        size             BIGINT,
        width            INTEGER,
        height           INTEGER,
        duration         DOUBLE PRECISION,
        download_status  TEXT,                   -- last outcome when no file was stored
        download_error   TEXT,
        PRIMARY KEY (account_id, chat_id, message_id),
        FOREIGN KEY (account_id, chat_id, message_id)
            REFERENCES messages (account_id, chat_id, id) ON DELETE CASCADE
    );
    CREATE INDEX media_file_key ON media (file_key) WHERE file_key IS NOT NULL;

    -- Downloaded copies: which machine (host + OS user) holds the file, and where.
    CREATE TABLE media_files (
        account_id     BIGINT NOT NULL,
        chat_id        BIGINT NOT NULL,
        message_id     BIGINT NOT NULL,
        host           TEXT   NOT NULL,
        os_user        TEXT   NOT NULL,
        path           TEXT   NOT NULL,
        size           BIGINT,
        downloaded_at  TEXT   NOT NULL,
        PRIMARY KEY (account_id, chat_id, message_id, host, os_user),
        FOREIGN KEY (account_id, chat_id, message_id)
            REFERENCES media (account_id, chat_id, message_id) ON DELETE CASCADE
    );
    CREATE INDEX media_files_node ON media_files (host, os_user);

    -- Newest message of each chat as of the last time Telegram was asked.
    CREATE TABLE sync_state (
        account_id       BIGINT NOT NULL,
        chat_id          BIGINT NOT NULL REFERENCES peers (id) ON DELETE CASCADE,
        head_message_id  BIGINT NOT NULL,     -- nothing newer existed at checked_at (0 = empty)
        checked_at       TEXT   NOT NULL,
        PRIMARY KEY (account_id, chat_id)
    );

    -- Message ID ranges stored without gaps: every message of the chat with
    -- start_id <= id <= end_id is in `messages`. start_id = 0 is the start of history.
    CREATE TABLE sync_ranges (
        account_id  BIGINT NOT NULL,
        chat_id     BIGINT NOT NULL,
        start_id    BIGINT NOT NULL,
        end_id      BIGINT NOT NULL,
        PRIMARY KEY (account_id, chat_id, start_id),
        CHECK (start_id <= end_id)
    );
    """,
)
