"""Wall-clock time + zone name → an exact instant.

The model only reports what the email says ("2026-09-30T11:25" plus
"America/Los_Angeles", or no zone at all). Every conversion across zones and
DST happens here with the tz database, never inside the model.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_OFFSET = re.compile(r"(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?", re.I)


def parse_zone(name: str | None) -> tzinfo | None:
    """IANA name ("Asia/Tokyo") or fixed offset ("+09:00", "UTC+9"); None if unusable."""
    if not name:
        return None
    name = name.strip()
    if name.upper() in {"UTC", "GMT", "Z"}:
        return timezone.utc
    m = _OFFSET.fullmatch(name)
    if m:
        off = timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        if off > timedelta(hours=14):
            return None
        return timezone(-off if m.group(1) == "-" else off)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def system_zone() -> str:
    """IANA name of the Mac's current zone (from the /etc/localtime symlink)."""
    try:
        target = os.readlink("/etc/localtime")
        i = target.find("zoneinfo/")
        if i >= 0:
            name = target[i + len("zoneinfo/"):]
            ZoneInfo(name)
            return name
    except (OSError, ZoneInfoNotFoundError, ValueError):
        pass
    return "UTC"


def localize(wall: str, zone: str | None, default_zone: str) -> tuple[datetime, str, bool]:
    """Resolve a wall-clock string to an aware datetime.

    Returns (instant, zone label used, whether the email stated the zone).
    With no usable zone the user's configured zone applies.
    """
    naive = datetime.fromisoformat(wall.strip())
    if naive.tzinfo is not None:
        return naive, naive.strftime("UTC%z"), True
    tz = parse_zone(zone)
    stated = tz is not None
    if tz is None:
        tz = ZoneInfo(default_zone)
        zone = default_zone
    # fold=0 (the default) settles the two DST edge cases: a wall time inside a
    # spring-forward gap lands just after the gap (02:30 → 03:30), and an
    # ambiguous fall-back time takes its first, earlier occurrence — the safe
    # side for a reminder.
    return naive.replace(tzinfo=tz), zone, stated


def parse_date(text: str) -> date:
    return date.fromisoformat(text.strip()[:10])
