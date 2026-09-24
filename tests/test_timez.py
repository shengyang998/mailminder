from datetime import datetime, timedelta, timezone

from mailminder.timez import localize, parse_zone

UTC = timezone.utc


def test_stated_iana_zone_converts_to_the_right_instant():
    dt, zone, stated = localize("2026-09-30T11:25", "America/Los_Angeles", "Asia/Shanghai")
    assert stated and zone == "America/Los_Angeles"
    assert dt.astimezone(UTC) == datetime(2026, 9, 30, 18, 25, tzinfo=UTC)  # PDT = UTC-7


def test_missing_zone_falls_back_to_the_user_zone():
    dt, zone, stated = localize("2026-09-25T15:00", None, "Asia/Shanghai")
    assert not stated and zone == "Asia/Shanghai"
    assert dt.astimezone(UTC) == datetime(2026, 9, 25, 7, 0, tzinfo=UTC)


def test_garbage_zone_is_treated_as_missing():
    _, zone, stated = localize("2026-09-25T15:00", "Beijing Time-ish", "Asia/Shanghai")
    assert not stated and zone == "Asia/Shanghai"


def test_fixed_offsets_and_utc_are_accepted():
    assert parse_zone("+09:00").utcoffset(None) == timedelta(hours=9)
    assert parse_zone("UTC-5").utcoffset(None) == timedelta(hours=-5)
    assert parse_zone("GMT+5:30").utcoffset(None) == timedelta(hours=5, minutes=30)
    assert parse_zone("UTC") is UTC
    assert parse_zone("+15:00") is None


def test_explicit_offset_in_the_wall_string_wins():
    dt, _, stated = localize("2026-09-30T09:00+09:00", None, "Asia/Shanghai")
    assert stated and dt.astimezone(UTC) == datetime(2026, 9, 30, 0, 0, tzinfo=UTC)


def test_winter_and_summer_offsets_differ_for_the_same_zone():
    summer, _, _ = localize("2026-07-01T10:00", "Europe/London", "Asia/Shanghai")
    winter, _, _ = localize("2026-12-01T10:00", "Europe/London", "Asia/Shanghai")
    assert summer.astimezone(UTC).hour == 9 and winter.astimezone(UTC).hour == 10


def test_spring_forward_gap_lands_after_the_gap():
    # 2027-03-14 02:30 does not exist in Los Angeles (clocks jump 02:00 → 03:00).
    dt, _, _ = localize("2027-03-14T02:30", "America/Los_Angeles", "Asia/Shanghai")
    assert dt.astimezone(UTC) == datetime(2027, 3, 14, 10, 30, tzinfo=UTC)  # = 03:30 PDT


def test_fall_back_ambiguity_takes_the_earlier_occurrence():
    # 2026-11-01 01:30 happens twice in Los Angeles; the first one is PDT (UTC-7).
    dt, _, _ = localize("2026-11-01T01:30", "America/Los_Angeles", "Asia/Shanghai")
    assert dt.astimezone(UTC) == datetime(2026, 11, 1, 8, 30, tzinfo=UTC)
