from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from mailminder.mailbox import Message
from mailminder.plan import CANCELLED_PREFIX, plan

SH = "Asia/Shanghai"
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=ZoneInfo(SH))
UTC = timezone.utc
MSG = Message(mailbox="INBOX", uid=1, message_id="m1@example.com",
              date=datetime(2026, 9, 20, 9, 0, tzinfo=timezone(timedelta(hours=-7))),
              sender="United Airlines <notifications@united.com>", subject="Your trip", text="")


def raw(**kw):
    base = dict(title="航班 UA857 旧金山→上海", kind="travel", all_day=False, start="2026-09-30T11:25",
                end=None, timezone="America/Los_Angeles", end_timezone=None, location=None,
                status="confirmed", key="UA857", evidence="Departs SFO Sep 30 11:25 AM", notes=None)
    base.update(kw)
    return base


def test_flight_with_stated_zone_and_arrival_in_another_zone():
    p = plan(raw(end="2026-10-01T15:35", end_timezone=SH), MSG, SH, NOW)
    assert p.event.start.astimezone(UTC) == datetime(2026, 9, 30, 18, 25, tzinfo=UTC)
    assert p.event.end.astimezone(UTC) == datetime(2026, 10, 1, 7, 35, tzinfo=UTC)
    assert p.event.alarms == [timedelta(days=-1), timedelta(hours=-3)]
    assert "当地 2026-09-30 11:25（America/Los_Angeles）＝ Asia/Shanghai 2026-10-01 02:25" in p.event.description
    assert p.event.url == "message://%3Cm1@example.com%3E"


def test_no_zone_uses_the_user_zone_and_says_so():
    p = plan(raw(title="项目评审会", kind="meeting", start="2026-09-25T15:00", timezone=None, key="评审会"),
             MSG, SH, NOW)
    assert p.event.start.astimezone(UTC) == datetime(2026, 9, 25, 7, 0, tzinfo=UTC)
    assert p.event.alarms == [timedelta(minutes=-30)]
    assert "邮件没写时区" in p.event.description


def test_same_item_from_two_emails_gets_one_uid_and_a_new_time_gets_another():
    a = plan(raw(key="UA 857"), MSG, SH, NOW)
    b = plan(raw(key="ua857", title="UA857 起飞"), MSG, SH, NOW)
    c = plan(raw(key="UA857", start="2026-09-30T13:00"), MSG, SH, NOW)
    assert a.uid == b.uid != c.uid


def test_all_day_deadline_has_morning_alarms_and_floating_date():
    p = plan(raw(title="物业费缴费截止", kind="deadline", all_day=True, start="2026-09-30", timezone=None,
                 key="物业费"), MSG, SH, NOW)
    assert p.event.start == date(2026, 9, 30) and p.event.end == date(2026, 10, 1)
    assert p.event.alarms == [timedelta(hours=-15), timedelta(hours=9)]


def test_all_day_range_end_is_made_exclusive():
    p = plan(raw(kind="event", all_day=True, start="2026-10-01", end="2026-10-03"), MSG, SH, NOW)
    assert p.event.end == date(2026, 10, 4)


def test_past_items_are_skipped():
    assert plan(raw(start="2026-09-24T11:00", timezone=None), MSG, SH, NOW) == "已经过去"
    assert plan(raw(all_day=True, start="2026-09-23"), MSG, SH, NOW) == "已经过去"


def test_today_all_day_deadline_is_kept_but_past_alarms_dropped():
    p = plan(raw(kind="deadline", all_day=True, start="2026-09-24"), MSG, SH, NOW)
    assert p.event.alarms == []  # 09:00 today and yesterday are both gone at noon


def test_alarms_already_past_are_dropped_and_imminent_items_still_alert():
    p = plan(raw(start="2026-09-24T14:00", timezone=None), MSG, SH, NOW)  # travel in 2 h
    assert p.event.alarms == [timedelta(0)]


def test_cancellation_matches_the_original_uid_and_has_no_alarm():
    original = plan(raw(), MSG, SH, NOW)
    cancelled = plan(raw(status="cancelled"), MSG, SH, NOW)
    assert cancelled.uid == original.uid
    assert cancelled.event.title.startswith(CANCELLED_PREFIX) and cancelled.event.alarms == []


def test_garbage_is_rejected_with_a_reason():
    assert plan(raw(title=""), MSG, SH, NOW) == "没有标题"
    assert plan(raw(start="next friday"), MSG, SH, NOW).startswith("时间看不懂")
    assert plan(raw(start="2031-01-01T10:00"), MSG, SH, NOW).startswith("太远")


def test_nonsense_end_is_dropped():
    p = plan(raw(end="2026-09-29T10:00"), MSG, SH, NOW)
    assert p.event.end is None
