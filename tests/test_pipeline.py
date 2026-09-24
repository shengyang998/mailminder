"""The run loop end to end with in-memory mailbox, calendar and model."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from mailminder import pipeline
from mailminder.extract import HarnessError
from mailminder.ledger import Ledger
from mailminder.mailbox import Message

SH = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=SH)
CAL = "https://cal.example/home/mm/"


def ev(title, start, key, status="confirmed", tz=None, kind="meeting"):
    return dict(title=title, kind=kind, all_day=False, start=start, end=None, timezone=tz, end_timezone=None,
                location=None, status=status, key=key, evidence=title, notes=None)


class FakeServer:
    def __init__(self):
        self.uidvalidity = 1
        self.messages: dict[int, Message] = {}

    def add(self, uid, subject):
        self.messages[uid] = Message("INBOX", uid, f"{subject}@mail", NOW - timedelta(hours=1), "S <s@x>", subject,
                                     f"body of {subject}")


class FakeMailbox:
    def __init__(self, server):
        self.server = server

    def __call__(self, host, port, username, password):
        assert password == "mail-secret"
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def select(self, mailbox):
        return self.server.uidvalidity, max(self.server.messages, default=0) + 1

    def uids_since(self, day):
        return sorted(self.server.messages)

    def uids_after(self, last):
        return sorted(u for u in self.server.messages if u > last)

    def fetch(self, mailbox, uids):
        return [self.server.messages[u] for u in uids if u in self.server.messages]


class FakeCalendar:
    def __init__(self):
        self.items: dict[str, str] = {}

    def __call__(self, url, username, password):
        assert password == "cal-secret"
        return self

    def put_event(self, href, ics, *, create_only):
        if create_only and href in self.items:
            return 412, None
        created = href not in self.items
        self.items[href] = ics
        return (201 if created else 204), '"etag"'


class FakeModel:
    def __init__(self, answers):
        self.answers = answers  # subject -> events, or an Exception to raise
        self.calls = 0

    def __call__(self, messages, ai, zone, language, now, work_dir):
        self.calls += 1
        out = {}
        for m in messages:
            a = self.answers.get(m.subject, [])
            if isinstance(a, Exception):
                raise a
            if a is not None:
                out[m.key] = a
        return out


def secret(service, account):
    return "cal-secret" if service == "mailminder.calendar" else "mail-secret"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MAILMINDER_HOME", str(tmp_path))
    cfg = {"timezone": "Asia/Shanghai", "language": "zh-Hans", "lookback_days": 7, "max_per_run": 50,
           "batch_size": 2, "ai": {"harness": "claude", "path": "x", "model": "m", "effort": "", "timeout": 5},
           "calendar": {"url": "https://cal.example", "username": "me", "calendar_url": CAL},
           "accounts": [{"name": "Mail", "host": "imap.example", "port": 993, "username": "me",
                         "mailboxes": ["INBOX"]}]}
    server, cal = FakeServer(), FakeCalendar()

    def run(model, **kw):
        return pipeline.run(cfg, now=NOW, mailbox_factory=FakeMailbox(server), caldav_factory=cal,
                            extractor=model, secret=secret, log=lambda *_: None, **kw)

    return cfg, server, cal, run, tmp_path


def test_first_run_then_only_new_mail(env):
    cfg, server, cal, run, _ = env
    server.add(10, "评审会")
    server.add(11, "广告")
    model = FakeModel({"评审会": [ev("项目评审会", "2026-09-25T15:00", "评审会")], "广告": []})
    r = run(model)
    assert (r.fetched, r.extracted, r.created, r.error) == (2, 2, 1, None)
    assert len(cal.items) == 1 and "DTSTART:20260925T070000Z" in next(iter(cal.items.values()))
    assert run(model).fetched == 0 and model.calls == 1  # nothing new → no model call
    server.add(12, "面试")
    model.answers["面试"] = [ev("面试", "2026-10-02T10:00", "acme", tz="America/Los_Angeles")]
    r = run(model)
    assert (r.fetched, r.created) == (1, 1) and len(cal.items) == 2


def test_same_item_in_two_emails_is_written_once(env):
    _, server, cal, run, _ = env
    server.add(1, "订票确认")
    server.add(2, "出行提醒")
    flight = ev("航班 UA857", "2026-09-30T11:25", "UA857", tz="America/Los_Angeles", kind="travel")
    r = run(FakeModel({"订票确认": [flight], "出行提醒": [dict(flight, title="UA857 明天起飞", key="ua 857")]}))
    assert (r.created, r.duplicates) == (1, 1) and len(cal.items) == 1


def test_cancellation_rewrites_the_existing_event(env):
    _, server, cal, run, _ = env
    server.add(1, "预约成功")
    run(FakeModel({"预约成功": [ev("洁牙检查", "2026-09-28T14:30", "洁牙")]}))
    server.add(2, "预约取消")
    r = run(FakeModel({"预约取消": [ev("洁牙检查", "2026-09-28T14:30", "洁牙", status="cancelled")]}))
    assert r.cancelled == 1
    (ics,) = cal.items.values()
    assert "SUMMARY:【已取消】洁牙检查" in ics and "VALARM" not in ics


def test_cancellation_without_an_original_is_ignored(env):
    _, server, cal, run, _ = env
    server.add(1, "取消")
    r = run(FakeModel({"取消": [ev("不存在的会", "2026-09-28T14:30", "x", status="cancelled")]}))
    assert cal.items == {} and r.skipped[0][2] == "没有对应的已建日程"


def test_dry_run_writes_nothing_and_the_real_run_reuses_the_answer(env):
    _, server, cal, run, _ = env
    server.add(1, "评审会")
    model = FakeModel({"评审会": [ev("项目评审会", "2026-09-25T15:00", "评审会")]})
    r = run(model, dry_run=True)
    assert cal.items == {} and [a for _, _, a in r.planned] == ["新建"]
    r = run(model)
    assert r.created == 1 and model.calls == 1


def test_model_failure_retries_then_gives_up_without_blocking_later_mail(env):
    _, server, cal, run, tmp = env
    server.add(1, "坏邮件")
    model = FakeModel({"坏邮件": HarnessError("Not logged in")})
    for _ in range(3):
        r = run(model)
        assert r.error == "Not logged in"
    server.add(2, "评审会")
    model.answers["评审会"] = [ev("项目评审会", "2026-09-25T15:00", "评审会")]
    r = run(model)
    assert r.error is None and r.created == 1
    ledger = Ledger(tmp / "state.db")
    assert ledger.counts() == {"skipped": 1, "extracted": 1}


def test_unanswered_message_is_retried_next_run(env):
    _, server, cal, run, _ = env
    server.add(1, "漏答")
    model = FakeModel({"漏答": None})
    run(model)
    model.answers["漏答"] = [ev("会", "2026-09-25T15:00", "k")]
    assert run(model).created == 1


def test_event_deleted_by_the_user_is_not_recreated(env):
    _, server, cal, run, _ = env
    item = ev("项目评审会", "2026-09-25T15:00", "评审会")
    server.add(1, "通知")
    run(FakeModel({"通知": [item]}))
    cal.items.clear()  # the user deleted it on the phone
    server.add(2, "再次提醒")
    r = run(FakeModel({"再次提醒": [item]}))
    assert cal.items == {} and r.duplicates == 1


def test_uidvalidity_change_rescans_without_asking_the_model_again(env):
    _, server, cal, run, _ = env
    server.add(1, "评审会")
    model = FakeModel({"评审会": [ev("项目评审会", "2026-09-25T15:00", "评审会")]})
    run(model)
    server.uidvalidity = 2
    r = run(model)
    assert model.calls == 1 and r.fetched == 0


def test_per_run_budget_leaves_the_rest_for_the_next_run(env):
    cfg, server, cal, run, _ = env
    cfg["max_per_run"] = 2
    for uid in (1, 2, 3):
        server.add(uid, f"会{uid}")
    model = FakeModel({f"会{u}": [ev(f"会{u}", f"2026-09-2{5 + u}T10:00", f"k{u}")] for u in (1, 2, 3)})
    assert run(model).created == 2
    assert run(model).created == 1
    assert len(cal.items) == 3


def test_repeated_failures_put_one_alert_on_the_calendar(env):
    cfg, server, cal, run, _ = env
    server.add(1, "x")
    model = FakeModel({"x": HarnessError("Not logged in")})
    run(model)
    run(model)
    assert cal.items == {}
    server.add(2, "y")  # a fresh message so the third run fails again
    model.answers["y"] = HarnessError("Not logged in")
    run(model)
    run(model)
    alerts = [ics for href, ics in cal.items.items() if "alert" in href]
    assert len(alerts) == 1 and "Mailminder 出错了" in alerts[0]


def test_concurrent_runs_are_refused(env):
    _, server, cal, run, _ = env
    with pipeline.run_lock():
        with pytest.raises(pipeline.RunLocked):
            run(FakeModel({}))
