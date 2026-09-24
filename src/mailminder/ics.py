"""iCalendar (RFC 5545) text for the events Mailminder writes.

Timed events are written in UTC ("Z"). The instant is what an alert needs, and
every client renders UTC in the device's own zone, so a flight leaving SFO at
11:25 local shows at the right Beijing time without shipping VTIMEZONE blocks.
All-day events are floating DATE values and are never converted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

DEFAULT_DURATION = timedelta(hours=1)


@dataclass
class Event:
    uid: str
    title: str
    start: datetime | date  # aware datetime (timed) or date (all-day)
    end: datetime | date | None = None  # exclusive; None → 1 h / 1 day
    description: str = ""
    location: str = ""
    url: str = ""
    alarms: list[timedelta] = field(default_factory=list)  # relative to start; negative = before

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, datetime)


def escape_text(s: str) -> str:
    return (
        s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
        .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    )


def fold(line: str) -> str:
    """Fold at 75 octets (RFC 5545 §3.1) without splitting a UTF-8 sequence."""
    parts, cur, size, limit = [], [], 0, 75
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > limit:
            parts.append("".join(cur))
            cur, size = [" "], 1  # the continuation space counts toward the 75
        cur.append(ch)
        size += n
    parts.append("".join(cur))
    return "\r\n".join(parts)


def utc_stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def duration(td: timedelta) -> str:
    """RFC 5545 dur-value, e.g. -PT30M, -P1D, PT9H."""
    total = int(td.total_seconds())
    sign, total = ("-" if total < 0 else ""), abs(total)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    out = f"{sign}P" + (f"{days}D" if days else "")
    if hours or minutes or seconds or not days:
        out += "T" + (f"{hours}H" if hours else "") + (f"{minutes}M" if minutes else "")
        if seconds or not (hours or minutes):
            out += f"{seconds}S"
    return out


def to_ics(ev: Event, now: datetime | None = None) -> str:
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Mailminder//Mailminder//EN",
        "CALSCALE:GREGORIAN", "BEGIN:VEVENT",
        f"UID:{ev.uid}", f"DTSTAMP:{utc_stamp(now or datetime.now(timezone.utc))}",
    ]
    if ev.all_day:
        end = ev.end if ev.end and ev.end > ev.start else ev.start + timedelta(days=1)
        lines += [f"DTSTART;VALUE=DATE:{ev.start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}"]
    else:
        end = ev.end if ev.end and ev.end > ev.start else ev.start + DEFAULT_DURATION
        lines += [f"DTSTART:{utc_stamp(ev.start)}", f"DTEND:{utc_stamp(end)}"]
    lines.append(f"SUMMARY:{escape_text(ev.title)}")
    if ev.location:
        lines.append(f"LOCATION:{escape_text(ev.location)}")
    if ev.description:
        lines.append(f"DESCRIPTION:{escape_text(ev.description)}")
    if ev.url:
        lines.append(f"URL:{ev.url}")
    for off in ev.alarms:
        # iCloud drops a DISPLAY alarm that has no DESCRIPTION.
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{escape_text(ev.title)}",
                  f"TRIGGER:{duration(off)}", "END:VALARM"]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(fold(line) for line in lines) + "\r\n"
