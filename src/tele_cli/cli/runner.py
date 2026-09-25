"""Common main loop: load .env, parse, dispatch, print JSON (or text), map errors to exit codes."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any

from ..config import load_env_files
from ..errors import TeleError
from ..output import dump_json

Payload = dict[str, Any]


def run(
    build_parser: Callable[[], argparse.ArgumentParser],
    argv: Sequence[str] | None,
    dispatch: Callable[[argparse.Namespace], Payload],
    render_text: Callable[[argparse.Namespace, Payload], str],
) -> int:
    os.umask(0o077)  # config, session, database and media stay private to the user
    load_env_files()  # before the parser: option defaults read the environment
    args = build_parser().parse_args(argv)
    try:
        payload = dispatch(args)
        if getattr(args, "format", "json") == "text":
            print(render_text(args, payload))
        else:
            dump_json(payload)
        return 0
    except TeleError as exc:
        dump_json(exc.as_dict(), stream=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        dump_json(
            {"ok": False, "error": {"code": "interrupted", "message": "Operation cancelled."}},
            stream=sys.stderr,
        )
        return 130
