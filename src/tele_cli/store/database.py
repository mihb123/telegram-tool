"""Database backends: a local SQLite file, or a PostgreSQL server shared by many machines.

SQL in this package is written once, in the subset both dialects understand, with ``?``
and ``:name`` placeholders; they are translated for PostgreSQL on the fly. psycopg is only
imported for PostgreSQL, so SQLite use needs nothing beyond the standard library.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..config import database_path, database_url
from ..errors import TeleError
from ..values import to_iso, utc_now
from .migrations import MIGRATIONS

SCHEMA_VERSION = len(MIGRATIONS)
Params = Sequence[Any] | dict[str, Any]


def fold(value: str | None) -> str:
    """Lowercase and strip diacritics so "doi soat" matches "Đối soát"."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value.replace("đ", "d").replace("Đ", "D"))
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def store_error(exc: BaseException, label: str, hint: str | None = None) -> TeleError:
    return TeleError(
        "store_error",
        f"Message store {label} failed: {exc}",
        exit_code=9,
        hint=hint or "Check disk space and permissions, or point TELE_DB at another file.",
    )


def invalid_sql(exc: BaseException, hint: str) -> TeleError:
    return TeleError("invalid_sql", f"SQL error: {exc}", exit_code=2, hint=hint)


def split_statements(script: str) -> Iterator[str]:
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            yield statement.strip()
            statement = ""


class Database:
    """Connection wrapper with one API for both dialects; driver errors become TeleError."""

    dialect: str
    label: str
    errors: tuple[type[BaseException], ...]
    connection_hint: str | None = None
    schema_hint: str

    def __init__(self) -> None:
        self._depth = 0

    # --- implemented per dialect -----------------------------------------------------

    def _execute(self, sql: str, params: Params) -> Any:
        raise NotImplementedError

    def _executemany(self, sql: str, rows: list[Params]) -> None:
        raise NotImplementedError

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        raise NotImplementedError
        yield

    @staticmethod
    def _first(row: Any) -> Any:
        raise NotImplementedError

    def table_exists(self, name: str) -> bool:
        raise NotImplementedError

    def lock(self, key: str) -> None:
        """Serialize writers on ``key`` until the current transaction ends."""

    def after_migrate(self) -> None:
        """Dialect-specific objects created once, after migrations ran."""

    def size_bytes(self) -> int:
        raise NotImplementedError

    def read_only_query(self, sql: str, max_rows: int) -> tuple[list[str], list[tuple]]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    # --- shared ------------------------------------------------------------------------

    @contextmanager
    def _guard(self) -> Iterator[None]:
        try:
            yield
        except TeleError:
            raise
        except self.errors as exc:
            raise store_error(exc, self.label, self.connection_hint) from exc

    def execute(self, sql: str, params: Params = ()) -> int:
        """Execute one statement and return its affected-row count when available."""
        with self._guard():
            return self._execute(sql, params).rowcount

    def executemany(self, sql: str, rows: Iterable[Params]) -> None:
        rows = list(rows)
        if rows:
            with self._guard():
                self._executemany(sql, rows)

    def all(self, sql: str, params: Params = ()) -> list[Any]:
        with self._guard():
            return self._execute(sql, params).fetchall()

    def one(self, sql: str, params: Params = ()) -> Any | None:
        with self._guard():
            return self._execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Params = ()) -> Any:
        row = self.one(sql, params)
        return None if row is None else self._first(row)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group writes atomically; nested calls join the outer transaction."""
        if self._depth:
            yield
            return
        self._depth = 1
        try:
            with self._guard(), self._transaction():
                yield
        finally:
            self._depth = 0

    def migrate(self) -> None:
        if self.table_exists("schema_migrations"):
            version = self.scalar("SELECT MAX(version) FROM schema_migrations") or 0
            if version == SCHEMA_VERSION:
                return
        with self.transaction():
            self.lock("tele:schema")  # several machines may start at once
            self.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            version = self.scalar("SELECT MAX(version) FROM schema_migrations") or 0
            if version > SCHEMA_VERSION:
                raise store_error(
                    RuntimeError(f"schema v{version} is newer than this tele (v{SCHEMA_VERSION})"),
                    self.label,
                    "Upgrade tele on this machine.",
                )
            for number in range(version + 1, SCHEMA_VERSION + 1):
                for statement in split_statements(MIGRATIONS[number - 1]):
                    self.execute(statement)
                self.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (number, to_iso(utc_now())),
                )
            self.after_migrate()


class SQLiteDatabase(Database):
    dialect = "sqlite"
    errors = (sqlite3.Error,)
    schema_hint = 'List tables with: tele-local sql "SELECT name, sql FROM sqlite_master"'

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.label = str(path)
        with self._guard():
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.connection = self._connect(path)
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            target, uri = f"{path.resolve().as_uri()}?mode=ro", True
        else:
            target, uri = str(path), False
        connection = sqlite3.connect(target, uri=uri, isolation_level=None, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.create_function("fold", 1, fold, deterministic=True)
        return connection

    def _execute(self, sql: str, params: Params) -> Any:
        return self.connection.execute(sql, params)

    def _executemany(self, sql: str, rows: list[Params]) -> None:
        self.connection.executemany(sql, rows)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")  # take the write lock up front
        try:
            yield
        except BaseException:
            with suppress(sqlite3.Error):
                self.connection.execute("ROLLBACK")
            raise
        self.connection.execute("COMMIT")

    @staticmethod
    def _first(row: Any) -> Any:
        return row[0]

    def table_exists(self, name: str) -> bool:
        return bool(self.scalar("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)))

    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    def read_only_query(self, sql: str, max_rows: int) -> tuple[list[str], list[tuple]]:
        with self._guard():
            connection = self._connect(self.path, read_only=True)
        connection.row_factory = None
        try:
            cursor = connection.execute(sql)
            columns = [column[0] for column in cursor.description or ()]
            return columns, cursor.fetchmany(max_rows + 1)
        except sqlite3.Error as exc:
            raise invalid_sql(exc, self.schema_hint) from exc
        finally:
            connection.close()

    def close(self) -> None:
        self.connection.close()


_PLACEHOLDER = re.compile(r"\?|(?<![:\w]):([A-Za-z_]\w*)")


@lru_cache(maxsize=256)
def _pyformat(sql: str) -> str:
    """``?`` → ``%s`` and ``:name`` → ``%(name)s`` for psycopg."""
    return _PLACEHOLDER.sub(
        lambda match: f"%({match.group(1)})s" if match.group(1) else "%s", sql.replace("%", "%%")
    )


def _fold_translation() -> tuple[str, str]:
    """Latin letters with diacritics and their base letter, matching Python's ``fold``."""
    source, target = [], []
    for code in (*range(0x00C0, 0x0250), *range(0x1E00, 0x1F00)):
        char = chr(code)
        folded = fold(char)
        if len(folded) == 1 and folded != char.lower():
            source.append(char)
            target.append(folded)
    return "".join(source), "".join(target)


def mask_password(url: str) -> str:
    parts = urlsplit(url)
    if not parts.password:
        return url
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@", 1)
    return urlunsplit(parts._replace(netloc=netloc))


class PostgresDatabase(Database):
    dialect = "postgres"
    connection_hint = (
        "Check TELE_DATABASE_URL and that the database server is running "
        "(`make up` on the database host)."
    )
    schema_hint = (
        'List tables with: tele-local sql "SELECT table_name FROM information_schema.tables '
        "WHERE table_schema = 'public'\""
    )

    def __init__(self, url: str) -> None:
        import psycopg

        super().__init__()
        self._psycopg = psycopg
        self.errors = (psycopg.Error,)
        self.url = url
        self.label = mask_password(url)
        with self._guard():
            self._connect()

    def _connect(self) -> None:
        from psycopg.rows import dict_row

        self.connection = self._psycopg.connect(
            self.url,
            autocommit=True,
            row_factory=dict_row,
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=60,
            application_name="tele",
        )

    def _execute(self, sql: str, params: Params) -> Any:
        query = _pyformat(sql) if params else sql
        try:
            return self.connection.execute(query, params or None)
        except self._psycopg.OperationalError:
            # An idle connection (e.g. during `tele wait`) may have been dropped: retry once.
            if self._depth or not self.connection.broken:
                raise
            self._connect()
            return self.connection.execute(query, params or None)

    def _executemany(self, sql: str, rows: list[Params]) -> None:
        with self.connection.cursor() as cursor:
            cursor.executemany(_pyformat(sql), rows)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if self.connection.broken:  # dropped since the last statement; nothing to roll back
            self._connect()
        with self.connection.transaction():
            yield

    @staticmethod
    def _first(row: Any) -> Any:
        return next(iter(row.values()))

    def table_exists(self, name: str) -> bool:
        return self.scalar("SELECT to_regclass(?) IS NOT NULL", (name,))

    def lock(self, key: str) -> None:
        self.execute("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", (key,))

    def after_migrate(self) -> None:
        source, target = _fold_translation()
        self.execute(
            "CREATE OR REPLACE FUNCTION fold(value text) RETURNS text "
            "LANGUAGE sql IMMUTABLE PARALLEL SAFE AS "
            f"$$ SELECT lower(translate(coalesce(value, ''), '{source}', '{target}')) $$"
        )

    def size_bytes(self) -> int:
        return self.scalar("SELECT pg_database_size(current_database())")

    def read_only_query(self, sql: str, max_rows: int) -> tuple[list[str], list[tuple]]:
        from psycopg.rows import tuple_row

        try:
            with self.connection.transaction():
                self.connection.execute("SET TRANSACTION READ ONLY")
                self.connection.execute("SET LOCAL statement_timeout = '30s'")
                cursor = self.connection.cursor(row_factory=tuple_row)
                cursor.execute(sql)
                if cursor.description is None:
                    return [], []
                columns = [column.name for column in cursor.description]
                return columns, cursor.fetchmany(max_rows + 1)
        except self._psycopg.OperationalError as exc:
            if self.connection.broken:
                raise store_error(exc, self.label, self.connection_hint) from exc
            raise invalid_sql(exc, self.schema_hint) from exc
        except self._psycopg.Error as exc:
            raise invalid_sql(exc, self.schema_hint) from exc

    def close(self) -> None:
        self.connection.close()


def open_database() -> Database:
    url = database_url()
    if url is None:
        return SQLiteDatabase(database_path())
    if not url.startswith(("postgresql://", "postgres://")):
        raise TeleError(
            "configuration_error",
            "TELE_DATABASE_URL must be a postgresql:// URL (leave it empty for local SQLite).",
            exit_code=3,
        )
    return PostgresDatabase(url)
