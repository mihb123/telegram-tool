"""argparse value types and options shared by `tele` and `tele-local`."""

from __future__ import annotations

import argparse
import math
from collections.abc import Callable

from ..config import display_timezone
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
            return parse_time_bound(value, display_timezone(), end_of_day=end_of_day)
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
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="Output format (default: json)",
    )


def add_meta(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--meta",
        action="store_true",
        help="Every stored field per message (sender, reply, forward, media details, ...) "
        "instead of just id, time, text and media",
    )


def add_short_flags(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Give each long-only option a short form: its first letter, else its first two.

    Hand-written shorts and ``-h`` keep priority, earlier options win a tie, and an option
    whose two candidates are both taken stays long-only. Each subcommand is independent.
    """
    taken = parser._option_string_actions
    for action in parser._actions:
        if not action.option_strings or any(
            not option.startswith("--") for option in action.option_strings
        ):
            continue
        name = action.option_strings[0].lstrip("-").replace("-", "")
        for short in dict.fromkeys((f"-{name[:1]}", f"-{name[:2]}")):
            if short not in taken:
                action.option_strings.insert(0, short)
                taken[short] = action
                break
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            # Aliases map several names to one parser; visit each parser once.
            for subparser in {id(sub): sub for sub in action.choices.values()}.values():
                add_short_flags(subparser)
    return parser
