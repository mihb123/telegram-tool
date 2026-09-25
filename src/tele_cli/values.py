"""Plain values shared by both CLIs: chat targets and timestamps (no Telegram imports)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, tzinfo

from .errors import TeleError

_RELATIVE_TIME = re.compile(r"^(\d+)([mhdw])$")
_TIME_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_target(value: str) -> str | int:
    """Normalize a `-u` value: numeric peer ID, "me", or a username without "@"."""
    target = value.strip()
    if not target:
        raise TeleError("invalid_target", "Chat target cannot be empty.", exit_code=2)
    if target.lstrip("-").isdigit():
        return int(target)
    if target.lower() == "me":
        return "me"
    return target[1:] if target.startswith("@") else target


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def to_iso(value: datetime | None) -> str | None:
    """Stored timestamp format: ISO-8601 in UTC, so text order equals time order."""
    return value.astimezone(UTC).isoformat() if value else None


def display_time(value: str | None, zone: tzinfo) -> str | None:
    """Short printed form of a stored timestamp: "yyyy-mm-dd HH:MM:SS" in ``zone``."""
    return from_iso(value).astimezone(zone).strftime("%Y-%m-%d %H:%M:%S") if value else None


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def parse_time_bound(value: str, zone: tzinfo, *, end_of_day: bool = False) -> str:
    """Parse "30m", "2h", "3d", "1w", "2026-09-25" or an ISO datetime into stored format.

    Naive values are in ``zone``, the zone printed times use, so a copied ``time`` matches
    itself. With ``end_of_day`` a bare date means the start of the following day, so an
    exclusive upper bound still includes that date.
    """
    text = value.strip()
    relative = _RELATIVE_TIME.match(text.lower())
    if relative:
        amount, unit = relative.groups()
        moment = utc_now() - timedelta(**{_TIME_UNITS[unit]: int(amount)})
    else:
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=zone)
        if end_of_day and len(text) == 10:
            moment += timedelta(days=1)
    return to_iso(moment.replace(microsecond=0))
