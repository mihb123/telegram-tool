"""Local SQLite store of everything `tele` has fetched (no Telegram imports)."""

from .repository import LISTENER_STALE_SECONDS, MessageQuery, Store

__all__ = ["LISTENER_STALE_SECONDS", "MessageQuery", "Store"]
