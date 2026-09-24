"""Model output + source email → the calendar event to write (pure, no I/O).

The event UID is derived from the item's key and its resolved start, so the same
flight mentioned by a booking email and a reminder email lands on one calendar
entry, and a cancellation email finds the entry it cancels.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from email.utils import parseaddr
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .extract import KINDS
from .ics import Event
from .mailbox import Message
from .timez import localize, parse_date

ALARMS = {
    "travel": [timedelta(days=-1), timedelta(hours=-3)],
    "appointment": [timedelta(days=-1), timedelta(hours=-1)],
    "meeting": [timedelta(minutes=-30)],
    "deadline": [timedelta(days=-1), timedelta(hours=-2)],
    "event": [timedelta(days=-1), timedelta(hours=-2)],
    "other": [timedelta(hours=-1)],
}
ALL_DAY_ALARMS = [timedelta(hours=-15), timedelta(hours=9)]  # 09:00 the day before, 09:00 on the day
MAX_AHEAD = timedelta(days=3 * 365)
CANCELLED_PREFIX = "【已取消】"


@dataclass
class Planned:
    uid: str
    event: Event
    status: str  # confirmed | cancelled
    kind: str


def norm_key(key: str, title: str) -> str:
    return re.sub(r"[\s\-_:：·•|/,.，。()（）\[\]【】#＃]+", "", (key or title).lower())


def make_uid(key: str, title: str, when: str) -> str:
    return "mailminder-" + hashlib.sha1(f"{norm_key(key, title)}|{when}".encode()).hexdigest()[:24]


def message_url(message_id: str) -> str:
    return "message://" + quote(f"<{message_id}>", safe="@") if message_id else ""


def _sender_name(sender: str) -> str:
    name, addr = parseaddr(sender)
    return name or addr or sender


def plan(raw: dict, msg: Message, user_zone: str, now: datetime) -> Planned | str:
    """Planned event, or a short reason string when the item is skipped."""
    title = str(raw.get("title") or "").strip()
    if not title:
        return "没有标题"
    kind = raw.get("kind") if raw.get("kind") in KINDS else "other"
    status = "cancelled" if raw.get("status") == "cancelled" else "confirmed"
    tz = ZoneInfo(user_zone)
    notes = str(raw.get("notes") or "").strip()
    lines = [notes] if notes else []

    if raw.get("all_day"):
        try:
            start = parse_date(str(raw["start"]))
            last = parse_date(str(raw["end"])) if raw.get("end") else start
        except (ValueError, KeyError):
            return f"日期看不懂：{raw.get('start')!r}"
        end = (last if last >= start else start) + timedelta(days=1)  # DTEND is exclusive
        if end <= now.astimezone(tz).date():
            return "已经过去"
        if datetime.combine(start, time(), tz) - now > MAX_AHEAD:
            return "太远，疑似年份读错"
        alarms = [a for a in ALL_DAY_ALARMS if datetime.combine(start, time(), tz) + a > now]
        uid = make_uid(raw.get("key", ""), title, start.isoformat())
        lines.append(f"日期：{start:%Y-%m-%d}（全天）")
    else:
        try:
            start, zone, stated = localize(str(raw["start"]), raw.get("timezone"), user_zone)
        except (ValueError, KeyError):
            return f"时间看不懂：{raw.get('start')!r}"
        if start <= now:
            return "已经过去"
        if start - now > MAX_AHEAD:
            return "太远，疑似年份读错"
        end = None
        if raw.get("end"):
            try:
                end, _, _ = localize(str(raw["end"]), raw.get("end_timezone") or raw.get("timezone"), user_zone)
            except ValueError:
                end = None
            if end is not None and not (start < end <= start + timedelta(days=7)):
                end = None
        alarms = [a for a in ALARMS[kind] if start + a > now] or [timedelta(0)]
        uid = make_uid(raw.get("key", ""), title, start.astimezone(timezone.utc).strftime("%Y%m%dT%H%MZ"))
        mine = start.astimezone(tz)
        if not stated:
            lines.append(f"时间：{mine:%Y-%m-%d %H:%M}（邮件没写时区，按 {user_zone} 计）")
        elif start.utcoffset() != mine.utcoffset():
            wall = str(raw["start"]).replace("T", " ")
            lines.append(f"时间：当地 {wall}（{zone}）＝ {user_zone} {mine:%Y-%m-%d %H:%M}")
        else:
            lines.append(f"时间：{mine:%Y-%m-%d %H:%M}（{zone}）")

    if status == "cancelled":
        title, alarms = CANCELLED_PREFIX + title, []
    evidence = str(raw.get("evidence") or "").strip()
    if evidence:
        lines.append(f"原文：「{evidence}」")
    sent = f"，{msg.date:%Y-%m-%d}" if msg.date else ""
    lines.append(f"来自：{_sender_name(msg.sender)}《{msg.subject}》{sent}")
    lines.append("由 Mailminder 根据邮件自动添加")
    event = Event(uid=uid, title=title, start=start, end=end, description="\n".join(lines),
                  location=str(raw.get("location") or "").strip(), url=message_url(msg.message_id),
                  alarms=alarms)
    return Planned(uid=uid, event=event, status=status, kind=kind)
