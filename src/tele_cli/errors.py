from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TeleError(Exception):
    code: str
    message: str
    exit_code: int = 1
    hint: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            error["hint"] = self.hint
        error.update(self.details)
        return {"ok": False, "error": error}
