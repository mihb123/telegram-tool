"""Local SQLite store of everything `tele` has fetched (no Telegram imports)."""

from .repository import MessageQuery, Store

__all__ = ["MessageQuery", "Store"]
