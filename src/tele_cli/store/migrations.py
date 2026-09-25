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
    # 2 — Telegram fields without a column (forward, views, reactions, links...) as JSON
    """
    ALTER TABLE messages ADD COLUMN meta TEXT;
    """,
    # 3 — durable listener inbox and one active listener lease per account
    """
    CREATE TABLE inbox_events (
        account_id   BIGINT NOT NULL,
        event_id     BIGINT NOT NULL,
        chat_id      BIGINT NOT NULL,
        message_id   BIGINT NOT NULL,
        received_at  TEXT   NOT NULL,
        PRIMARY KEY (account_id, event_id),
        UNIQUE (account_id, chat_id, message_id),
        FOREIGN KEY (account_id, chat_id, message_id)
            REFERENCES messages (account_id, chat_id, id) ON DELETE CASCADE
    );
    CREATE INDEX inbox_events_chat
        ON inbox_events (account_id, chat_id, event_id);

    CREATE TABLE listeners (
        account_id   BIGINT NOT NULL,
        host         TEXT   NOT NULL,
        os_user      TEXT   NOT NULL,
        process_id   BIGINT NOT NULL,
        started_at   TEXT   NOT NULL,
        heartbeat_at TEXT   NOT NULL,
        PRIMARY KEY (account_id, host, os_user, process_id)
    );
    CREATE INDEX listeners_heartbeat ON listeners (account_id, heartbeat_at);
    """,
    # 4 — what an attachment is (photo, video, sticker, voice, ...); NULL for link previews.
    # Rows saved earlier only know the MIME type, so their kind is a best guess until
    # the message is fetched again.
    """
    ALTER TABLE media ADD COLUMN kind TEXT;
    UPDATE media SET kind = CASE
        WHEN type = 'WebPage' THEN NULL
        WHEN type = 'Photo' THEN 'photo'
        WHEN type IN ('Geo', 'GeoLive', 'Venue') THEN 'location'
        WHEN type <> 'Document' THEN lower(type)
        WHEN mime_type IN ('image/webp', 'application/x-tgsticker') THEN 'sticker'
        WHEN mime_type = 'audio/ogg' THEN 'voice'
        WHEN mime_type LIKE 'video/%' THEN 'video'
        WHEN mime_type LIKE 'audio/%' THEN 'audio'
        ELSE 'file'
    END;
    """,
    # 5 — last inbox cursor handed out per account. `inbox_events` rows disappear with their
    # message (ON DELETE CASCADE), so MAX(event_id) could go backwards and reuse a cursor.
    """
    CREATE TABLE inbox_sequence (
        account_id     BIGINT PRIMARY KEY,
        last_event_id  BIGINT NOT NULL
    );
    INSERT INTO inbox_sequence (account_id, last_event_id)
    SELECT account_id, MAX(event_id) FROM inbox_events GROUP BY account_id;
    """,
    # 6 — named inbox cursors (`tele-local wait --consumer NAME`), so an agent resumes where it
    # left off without having to keep the cursor itself.
    """
    CREATE TABLE inbox_consumers (
        account_id     BIGINT NOT NULL,
        name           TEXT   NOT NULL,
        last_event_id  BIGINT NOT NULL,
        updated_at     TEXT   NOT NULL,
        PRIMARY KEY (account_id, name)
    );
    """,
    # 7 — what a message is, readable without joining `media`: the attachment kind (photo,
    # video, sticker, ...), else service, emoji or text. Emoji-only text cannot be told apart
    # in SQL, so older ones stay 'text' until the message is fetched again.
    """
    ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'text';
    UPDATE messages SET kind = COALESCE(
        (SELECT md.kind FROM media md
         WHERE md.account_id = messages.account_id AND md.chat_id = messages.chat_id
           AND md.message_id = messages.id),
        CASE WHEN service_action IS NOT NULL THEN 'service' ELSE 'text' END
    );
    """,
)
