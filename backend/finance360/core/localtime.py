"""The Household's local date.

The container runs in UTC, so for about ten hours a day the UTC date lags Melbourne's. "Today" for
date validation and default as-at dates must therefore be the Household's local date, derived from
its IANA time zone, never the server's clock date. Pure apart from the optional clock read.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: Default for a new Household (the one Australian household this is built for).
DEFAULT_TIME_ZONE = "Australia/Melbourne"


class InvalidTimeZoneError(ValueError):
    """Not an IANA time zone name known to this system."""


def parse_time_zone(name: str) -> ZoneInfo:
    """The ``ZoneInfo`` for an IANA name such as ``Australia/Melbourne``; raise if unknown."""
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise InvalidTimeZoneError(f"Unknown time zone: {name!r}") from None


def local_today(time_zone: str, now: datetime | None = None) -> date:
    """The calendar date in ``time_zone`` at ``now`` (an aware datetime; default: the clock)."""
    moment = now if now is not None else datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("local_today needs an aware datetime")
    return moment.astimezone(parse_time_zone(time_zone)).date()
