"""What day it is where the caller is: the one reading of a clock a call's context takes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pinecall.types.refusal import DeclarationRefused

NOT_A_ZONE = "{zone!r} is not an IANA time zone (Europe/Madrid, America/Montevideo, UTC)"


def parse_zone(zone: str) -> ZoneInfo:
    """The zone this word names, or a refusal that shows how one is spelled."""
    try:
        return ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as unknown:
        raise DeclarationRefused(NOT_A_ZONE.format(zone=zone)) from unknown


def today_in(zone: str) -> date:
    """Today's date in that zone, off the wall clock."""
    return datetime.now(UTC).astimezone(parse_zone(zone)).date()
