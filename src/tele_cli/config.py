from __future__ import annotations

import getpass
import json
import os
import re
import socket
import tempfile
from dataclasses import dataclass
from datetime import timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DISPLAY_TIMEZONE = timezone(timedelta(hours=7))
_UTC_OFFSET = re.compile(r"^(?:UTC|GMT)?([+-])(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE)

# Source checkout root (…/src/tele_cli/config.py → …); inside a PyInstaller binary it is a
# temporary directory without pyproject.toml, so no project .env is read there.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    """Raised when Telegram API credentials are unavailable or invalid."""


@dataclass(frozen=True)
class Credentials:
    api_id: int
    api_hash: str


def config_dir() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "tele"


def data_dir() -> Path:
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "tele"


def config_path() -> Path:
    return config_dir() / "config.json"


def session_path() -> Path:
    override = os.environ.get("TELE_SESSION")
    return Path(override).expanduser().resolve() if override else data_dir() / "telegram"


def session_file_path() -> Path:
    path = session_path()
    return path if path.suffix == ".session" else path.with_suffix(".session")


def listener_socket_path() -> Path:
    """Private local socket used to send through the listener's Telegram connection."""
    override = os.environ.get("TELE_LISTENER_SOCKET")
    return Path(override).expanduser().resolve() if override else data_dir() / "listener.sock"


def listener_targets() -> tuple[str, ...]:
    """Return the de-duplicated conversations accepted by the persistent listener."""
    targets: list[str] = []
    seen: set[str] = set()
    for value in re.split(r"[,\s]+", os.environ.get("TELE_LISTEN_CHATS", "")):
        target = value.strip()
        if not target:
            continue
        key = target.removeprefix("@").casefold()
        if key not in seen:
            seen.add(key)
            targets.append(target)
    return tuple(targets)


@dataclass(frozen=True)
class Node:
    """The machine and OS account running tele; downloaded files are recorded per node."""

    host: str
    user: str

    def __str__(self) -> str:
        return f"{self.user}@{self.host}"


def current_node() -> Node:
    return Node(socket.gethostname(), getpass.getuser())


def env_files() -> list[Path]:
    """.env files in priority order: $TELE_ENV_FILE, the source checkout, ~/.config/tele."""
    files = []
    if os.environ.get("TELE_ENV_FILE"):
        files.append(Path(os.environ["TELE_ENV_FILE"]).expanduser())
    if (PROJECT_ROOT / "pyproject.toml").is_file():
        files.append(PROJECT_ROOT / ".env")
    files.append(config_dir() / ".env")
    return files


def _parse_env_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    key, separator, value = line.removeprefix("export ").partition("=")
    key, value = key.strip(), value.strip()
    if not separator or not key.isidentifier():
        return None
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return key, value[1:-1]
    return key, value.split(" #", 1)[0].rstrip()


def load_env_files() -> None:
    """Fill unset environment variables from .env files; real variables and earlier files win."""
    for path in env_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            if parsed := _parse_env_line(line):
                os.environ.setdefault(*parsed)


def database_url() -> str | None:
    """PostgreSQL URL of the shared store; unset means a local SQLite file."""
    return os.environ.get("TELE_DATABASE_URL", "").strip() or None


def database_path() -> Path:
    override = os.environ.get("TELE_DB")
    return Path(override).expanduser().resolve() if override else data_dir() / "messages.db"


def display_timezone() -> tzinfo:
    """$TELE_TIMEZONE as an offset ("+7", "UTC-05:30") or IANA name; UTC+7 when unset or invalid."""
    value = os.environ.get("TELE_TIMEZONE", "").strip()
    if not value:
        return DEFAULT_DISPLAY_TIMEZONE
    if offset := _UTC_OFFSET.match(value):
        sign, hours, minutes = offset.groups()
        hour_value, minute_value = int(hours), int(minutes or 0)
        if hour_value <= 23 and minute_value <= 59:
            delta = timedelta(hours=hour_value, minutes=minute_value)
            return timezone(-delta if sign == "-" else delta)
        return DEFAULT_DISPLAY_TIMEZONE
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return DEFAULT_DISPLAY_TIMEZONE


def default_media_root() -> Path:
    override = os.environ.get("TELE_MEDIA_DIR") or os.environ.get("TELE_DOWNLOAD_DIR")
    if override:
        return Path(override).expanduser().resolve()
    path = config_path()
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                config_media = payload.get("media_dir") or payload.get("download_dir")
                if config_media:
                    return Path(config_media).expanduser().resolve()
        except Exception:
            pass
    return (Path.home() / "telegram-files").resolve()


def _parse_api_id(value: Any) -> int:
    try:
        api_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError("Telegram API ID must be an integer.") from exc
    if api_id <= 0:
        raise ConfigError("Telegram API ID must be a positive integer.")
    return api_id


def validate_credentials(api_id: Any, api_hash: Any) -> Credentials:
    parsed_id = _parse_api_id(api_id)
    parsed_hash = str(api_hash or "").strip()
    if not parsed_hash:
        raise ConfigError("Telegram API hash cannot be empty.")
    return Credentials(parsed_id, parsed_hash)


def load_credentials() -> Credentials:
    env_id = os.environ.get("TELE_API_ID")
    env_hash = os.environ.get("TELE_API_HASH")
    if env_id is not None or env_hash is not None:
        if env_id is None or env_hash is None:
            raise ConfigError("Set both TELE_API_ID and TELE_API_HASH, or neither.")
        return validate_credentials(env_id, env_hash)

    path = config_path()
    if not path.exists():
        raise ConfigError(f"Credentials not configured. Run `tele configure` (expected {path}).")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read valid configuration from {path}: {exc}") from exc
    return validate_credentials(payload.get("api_id"), payload.get("api_hash"))


def save_credentials(credentials: Credentials) -> Path:
    directory = config_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    destination = config_path()
    payload = json.dumps(
        {"api_id": credentials.api_id, "api_hash": credentials.api_hash},
        ensure_ascii=False,
        indent=2,
    )
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=".config-", dir=directory)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
        temporary_path.chmod(0o600)
        os.replace(temporary_path, destination)
        destination.chmod(0o600)
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination


def secure_data_directory() -> Path:
    directory = data_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory


def secure_session_file() -> None:
    path = session_file_path()
    if path.exists():
        path.chmod(0o600)
