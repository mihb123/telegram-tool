"""Media downloads that never fetch the same file twice.

Each attachment is first resolved locally, in this order:

1. this machine already recorded a file for the message and it is still on disk;
2. this machine downloaded the same Telegram photo/document (``file_key``) for another message;
3. a file from an earlier run already sits at the destination path;
4. it is known to be too large for the current limit, or not downloadable at all.

Only what is left is downloaded from Telegram. Every file is recorded in ``media_files``
with the hostname and OS user of the machine holding it, so machines sharing a database
can see where each copy lives.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError

from ..config import default_media_root
from ..errors import TeleError
from ..store import Store

# Attachments without a photo/document behind them (link previews without an image, polls,
# locations, dice) have nothing to download.
FILELESS_MEDIA_TYPES = frozenset({"WebPage", "Geo", "GeoLive", "Venue", "Poll", "Dice"})


class _MediaSizeLimitExceeded(Exception):
    pass


def _safe_component(value: str, fallback: str) -> str:
    component = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return component[:100] or fallback


def prepare_media_directory(target_value: str, download_dir: Path | None) -> Path:
    """`-d` as given, otherwise ``<media root>/<target>``; created private if missing."""
    target = _safe_component(target_value.removeprefix("@"), "chat")
    directory = (download_dir or default_media_root() / target).resolve()
    existed = directory.exists()
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not existed:
            directory.chmod(0o700)
    except OSError as exc:
        raise TeleError(
            "media_directory_error",
            f"Cannot create media directory {directory}: {exc}",
            exit_code=8,
        ) from exc
    return directory


def _destination(message_id: int, media: Any, directory: Path) -> Path:
    """``<message id>_<file name>``; an existing file only counts when its size matches."""
    raw_extension = str(media["extension"] or "").lstrip(".")
    safe_extension = re.sub(r"[^A-Za-z0-9.]+", "", raw_extension)[:20]
    extension = f".{safe_extension}" if safe_extension else ""
    if media["name"]:
        name = _safe_component(Path(str(media["name"])).name, "media")
        if extension and not name.lower().endswith(extension.lower()):
            name += extension
    else:
        name = f"{_safe_component(str(media['type'] or 'media').lower(), 'media')}{extension}"
    destination = directory / f"{message_id}_{name}"
    if not destination.exists() or _is_complete(destination, media["size"]):
        return destination
    for suffix in range(1, 10_000):
        candidate = destination.with_name(f"{destination.stem}_{suffix}{destination.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"Cannot allocate a unique filename for {destination.name}")


def _is_complete(path: Path, expected_size: int | None) -> bool:
    return path.is_file() and (expected_size is None or path.stat().st_size == expected_size)


class MediaDownloader:
    """Ensures media of selected messages is on disk here, recording host and user per file."""

    def __init__(
        self,
        store: Store,
        chat_id: int,
        directory: Path,
        *,
        max_bytes: int,
        exact_directory: bool,
    ) -> None:
        self.store = store
        self.chat_id = chat_id
        self.directory = directory
        self.max_bytes = max_bytes
        # With -d the files must end up in that directory, so cached copies are copied there.
        self.exact_directory = exact_directory
        self.statuses: dict[int, str] = {}
        self._pending: dict[int, Any] = {}

    def resolve_locally(self, message_ids: list[int]) -> list[int]:
        """Settle everything possible without Telegram; return IDs that need a download."""
        with self.store.transaction():
            for media in self.store.media_rows(self.chat_id, message_ids):
                if not self._resolve(media):
                    self._pending[media["message_id"]] = media
        return list(self._pending)

    def _resolve(self, media: Any) -> bool:
        message_id, size = media["message_id"], media["size"]
        # Only copies recorded for this machine are reachable; other nodes keep their own.
        for copy in self.store.node_copies(self.chat_id, message_id, media["file_key"]):
            if _is_complete(Path(copy["path"]), size):
                self._finish(media, self._place(media, Path(copy["path"])), "existing")
                return True

        destination = _destination(message_id, media, self.directory)
        if destination.exists():
            self._finish(media, destination, "existing")
            return True
        if self.max_bytes and size is not None and size > self.max_bytes:
            self._finish(media, None, "skipped_too_large")
            return True
        if media["download_status"] == "not_downloadable" or (
            media["file_key"] is None and media["type"] in FILELESS_MEDIA_TYPES
        ):
            self._finish(media, None, "not_downloadable")
            return True
        return False

    def _place(self, media: Any, source: Path) -> Path:
        if not self.exact_directory or source.parent == self.directory:
            return source.resolve()
        destination = _destination(media["message_id"], media, self.directory)
        if not destination.exists():
            shutil.copy2(source, destination)
            destination.chmod(0o600)
        return destination.resolve()

    def _finish(self, media: Any, path: Path | None, status: str, error: str | None = None) -> None:
        if path is None:
            self.store.set_media_status(self.chat_id, media["message_id"], status, error)
        else:
            self.store.record_file(
                self.chat_id, media["message_id"], str(path), path.stat().st_size, status
            )
        self.statuses[media["message_id"]] = status

    async def download(
        self, client: TelegramClient, input_entity: Any, known: dict[int, Any]
    ) -> None:
        """Download pending media; ``known`` holds Telethon messages fetched this run."""
        missing = [message_id for message_id in self._pending if message_id not in known]
        if missing:
            for message in await client.get_messages(input_entity, ids=missing):
                if message is not None:
                    known[message.id] = message
        for message_id, media in self._pending.items():
            message = known.get(message_id)
            with self.store.transaction():
                # An album or forwarded copy may have fetched this very file a moment ago.
                if self._resolve(media):
                    continue
                if message is None or getattr(message, "media", None) is None:
                    self._finish(media, None, "error", "Message media is no longer available.")
                    continue
            await self._download_one(client, message, media)
        self._pending.clear()

    async def _download_one(self, client: TelegramClient, message: Any, media: Any) -> None:
        destination = _destination(message.id, media, self.directory)

        def enforce_size_limit(received: int, total: int) -> None:
            if self.max_bytes and (received > self.max_bytes or (total or 0) > self.max_bytes):
                raise _MediaSizeLimitExceeded

        status, path, error = "downloaded", None, None
        try:
            downloaded = await client.download_media(
                message, file=str(destination), progress_callback=enforce_size_limit
            )
        except _MediaSizeLimitExceeded:
            destination.unlink(missing_ok=True)
            status = "skipped_too_large"
        except FloodWaitError:
            destination.unlink(missing_ok=True)
            raise
        except (OSError, RPCError, ValueError) as exc:
            destination.unlink(missing_ok=True)
            status, error = "error", str(exc)
        else:
            if downloaded is None:
                destination.unlink(missing_ok=True)
                status = "not_downloadable"
            else:
                path = Path(str(downloaded)).expanduser().resolve()
                if path.is_file():
                    path.chmod(0o600)
                else:
                    status, error = "error", f"Telegram reported a missing download path: {path}"
                    path = None
        with self.store.transaction():
            self._finish(media, path, status, error)

    def summary(self) -> dict[str, int]:
        statuses = list(self.statuses.values())
        return {
            "downloaded": statuses.count("downloaded"),
            "existing": statuses.count("existing"),
            "skipped_too_large": statuses.count("skipped_too_large"),
            "not_downloadable": statuses.count("not_downloadable"),
            "errors": statuses.count("error"),
        }
