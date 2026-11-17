"""Normalize scalar values returned by different demo/parser versions."""

from typing import Any

from cs2demo_parser.models import DemoError


def integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def truth(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1"}


def team_number(value: Any) -> int | None:
    aliases = {"t": 2, "terrorist": 2, "ct": 3, "counterterrorist": 3}
    number = aliases.get(str(value).strip().lower(), integer(value))
    return number if number in (2, 3) else None


def steam_id(value: Any) -> str | None:
    # Never round SteamID64 through float: its 64-bit integer exceeds float's 53-bit precision.
    if isinstance(value, float) and value >= 2**53:
        raise DemoError("SteamID64 returned as float: player identity has lost precision")
    number = integer(value)
    return str(number) if number is not None and number > 0 else None


def text(value: Any, default: str = "") -> str:
    return default if value is None or str(value).lower() == "nan" else str(value)
