"""Round trip against the real server inside a throwaway calendar."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from live import caldav_account
from mailminder.caldav import CalDAV
from mailminder.ics import Event, to_ics

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def scratch():
    url, user, password = caldav_account()
    dav = CalDAV(url, user, password)
    home = dav.discover_home()
    cal = dav.make_calendar(home, "Mailminder 测试（自动删除）")
    yield dav, home, cal
    dav.delete(cal.url)


def test_calendar_appears_in_listing(scratch):
    dav, home, cal = scratch
    listed = {c.url: c for c in dav.calendars(home)}
    assert cal.url in listed and listed[cal.url].writable
    assert listed[cal.url].components == ["VEVENT"]


def test_create_is_idempotent_and_alarms_survive(scratch):
    dav, _, cal = scratch
    uid = uuid.uuid4().hex
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=3)
    ev = Event(uid=uid, title="测试：航班 UA857，SFO→PVG", start=start,
               description="第一行\n第二行; 含逗号, 分号", alarms=[timedelta(hours=-3)])
    href = f"{cal.url}{uid}.ics"
    status, _ = dav.put_event(href, to_ics(ev), create_only=True)
    assert status == 201
    again, _ = dav.put_event(href, to_ics(ev), create_only=True)
    assert again == 412  # server-side dedup
    body, etag = dav.get(href)
    assert "TRIGGER:-PT3H" in body and "BEGIN:VALARM" in body
    assert start.strftime("DTSTART:%Y%m%dT%H%M%SZ") in body
    assert etag
    assert dav.delete(href) is True
    assert dav.delete(href) is False
