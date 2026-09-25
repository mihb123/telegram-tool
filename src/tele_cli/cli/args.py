"""argparse value types and options shared by `tele` and `tele-local`."""

from __future__ import annotations

import argparse
import math
from collections.abc import Callable

from ..values import parse_time_bound


def bounded_int(name: str, low: int, high: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{name} must be between {low} and {high}")
        return number

    return parse


def positive_message_id(value: str) -> int:
    try:
        message_id = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("message ID must be an integer") from exc
    if message_id <= 0:
        raise argparse.ArgumentTypeError("message ID must be positive")
    return message_id


def non_negative_megabytes(value: str) -> float:
    try:
        megabytes = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("media size limit must be a number") from exc
    if not math.isfinite(megabytes) or megabytes < 0:
        raise argparse.ArgumentTypeError("media size limit must be a finite non-negative number")
    return megabytes


def time_bound(*, end_of_day: bool) -> Callable[[str], str]:
    def parse(value: str) -> str:
        try:
            return parse_time_bound(value, end_of_day=end_of_day)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                "use a duration like 30m/2h/3d/1w, a date YYYY-MM-DD or an ISO datetime"
            ) from exc

    return parse


def add_target(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    parser.add_argument(
        "-u",
        "--user",
        "--chat",
        dest="target",
        required=required,
        help="Username, @username, numeric user/group/channel ID, or 'me'",
    )


def add_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("json", "text"), default="json")
