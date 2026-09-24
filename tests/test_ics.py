from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from mailminder.ics import Event, duration, escape_text, fold, to_ics

NOW = datetime(2026, 9, 24, 4, 0, tzinfo=timezone.utc)


def unfold(text: str) -> list[str]:
    return text.replace("\r\n ", "").split("\r\n")


def test_durations():
    assert duration(timedelta(minutes=-30)) == "-PT30M"
    assert duration(timedelta(hours=-15)) == "-PT15H"
    assert duration(timedelta(days=-1)) == "-P1D"
    assert duration(timedelta(days=-1, hours=-3)) == "-P1DT3H"
    assert duration(timedelta(hours=9)) == "PT9H"
    assert duration(timedelta(0)) == "PT0S"


def test_escape_text():
    assert escape_text("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"


def test_fold_respects_75_octets_and_utf8_boundaries():
    line = "SUMMARY:" + "航班提醒" * 30
    folded = fold(line)
    for part in folded.split("\r\n"):
        assert len(part.encode("utf-8")) <= 75
        part.encode("utf-8").decode("utf-8")  # no split multi-byte sequence
    assert folded.replace("\r\n ", "") == line


def test_timed_event_is_written_in_utc_with_alarms():
    start = datetime(2026, 9, 30, 11, 25, tzinfo=ZoneInfo("America/Los_Angeles"))
    ev = Event(uid="abc", title="UA857 起飞, SFO→PVG", start=start,
               alarms=[timedelta(days=-1), timedelta(hours=-3)])
    lines = unfold(to_ics(ev, now=NOW))
    assert "DTSTART:20260930T182500Z" in lines
    assert "DTEND:20260930T192500Z" in lines  # default 1 h
    assert "SUMMARY:UA857 起飞\\, SFO→PVG" in lines
    assert lines.count("BEGIN:VALARM") == 2
    assert "TRIGGER:-P1D" in lines and "TRIGGER:-PT3H" in lines
    assert all(line != "DESCRIPTION:" for line in lines)
    assert to_ics(ev, now=NOW).endswith("END:VCALENDAR\r\n")


def test_all_day_event_uses_floating_dates_and_exclusive_end():
    ev = Event(uid="d1", title="缴费截止", start=date(2026, 9, 30), alarms=[timedelta(hours=-15)])
    lines = unfold(to_ics(ev, now=NOW))
    assert "DTSTART;VALUE=DATE:20260930" in lines
    assert "DTEND;VALUE=DATE:20261001" in lines
    assert "TRIGGER:-PT15H" in lines


def test_end_before_start_is_ignored():
    start = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)
    ev = Event(uid="x", title="t", start=start, end=start - timedelta(hours=1))
    assert "DTEND:20260930T110000Z" in unfold(to_ics(ev, now=NOW))
