"""One pass: new mail → model → calendar.

Order of work, each step idempotent:
1. scan   — per mailbox, UIDs newer than the cursor (first run: the lookback window)
2. extract — model answers cached per message in the ledger
3. cursor — advances only over messages that are finished, never past a retry
4. apply  — plan each cached answer and write it; dry runs stop before writing
"""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, keychain
from .caldav import CalDAV, CalDAVError
from .extract import HarnessError, extract
from .ics import Event, to_ics
from .ledger import Ledger
from .mailbox import IMAPMailbox, MailError, Message
from .plan import Planned, plan

ALERT_AFTER_FAILED_RUNS = 3


class RunLocked(RuntimeError):
    pass


@dataclass
class Scan:
    account: str
    mailbox: str
    uidvalidity: int
    uidnext: int
    uids: list[int]  # candidates fetched this run, ascending
    complete: bool  # False when the per-run budget left some for later


@dataclass
class Result:
    fetched: int = 0
    extracted: int = 0
    created: int = 0
    duplicates: int = 0
    cancelled: int = 0
    planned: list[tuple[str, Planned, str]] = field(default_factory=list)  # (subject, plan, action)
    skipped: list[tuple[str, str, str]] = field(default_factory=list)  # (subject, title, reason)
    error: str | None = None


@contextmanager
def run_lock():
    f = open(config.state_dir() / "run.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        raise RunLocked("上一次运行还没结束") from None
    try:
        yield
    finally:
        f.close()


def _scan(cfg, ledger: Ledger, since_days, budget, mailbox_factory, secret):
    scans: list[Scan] = []
    fresh: list[tuple[Scan, Message]] = []
    today = datetime.now(ZoneInfo(cfg["timezone"])).date()
    for acct in cfg["accounts"]:
        aid = config.mail_secret_account(acct)
        password = secret(keychain.MAIL_SERVICE, aid)
        if not password:
            raise config.ConfigError(f"钥匙串里没有 {aid} 的密码，运行 mailminder account password 补上")
        with mailbox_factory(acct["host"], int(acct.get("port", 993)), acct["username"], password) as mb:
            for mbox in acct.get("mailboxes") or ["INBOX"]:
                uidvalidity, uidnext = mb.select(mbox)
                cur = ledger.cursor(aid, mbox)
                if since_days is not None or cur is None or cur[0] != uidvalidity:
                    days = cfg["lookback_days"] if since_days is None else since_days
                    candidates = mb.uids_since(today - timedelta(days=days))
                else:
                    candidates = mb.uids_after(cur[1])
                candidates = [u for u in candidates if not ledger.seen(aid, mbox, uidvalidity, u)]
                take = candidates[:max(budget, 0)]
                budget -= len(take)
                scan = Scan(aid, mbox, uidvalidity, uidnext, take, len(take) == len(candidates))
                scans.append(scan)
                got = mb.fetch(mbox, take) if take else []
                for uid in set(take) - {m.uid for m in got}:  # deleted between SEARCH and FETCH
                    ledger.mark_seen(aid, mbox, uidvalidity, uid)
                for m in got:
                    if ledger.known(m.key):  # same mail already read in another folder/account
                        ledger.mark_seen(aid, mbox, uidvalidity, m.uid)
                    else:
                        fresh.append((scan, m))
    return scans, fresh


def _extract(cfg, ledger: Ledger, fresh, now, extractor, log) -> tuple[int, str | None]:
    done, size = 0, max(1, int(cfg["batch_size"]))
    for i in range(0, len(fresh), size):
        batch = fresh[i:i + size]
        log(f"让模型读第 {i + 1}–{i + len(batch)} 封（共 {len(fresh)} 封）……")
        try:
            answered = extractor([m for _, m in batch], cfg["ai"], cfg["timezone"], cfg["language"], now,
                                 str(config.work_dir()))
        except HarnessError as e:
            for scan, m in batch:
                if ledger.record_failure(scan.account, m, str(e)):
                    ledger.mark_seen(scan.account, scan.mailbox, scan.uidvalidity, m.uid)
            return done, str(e)  # the same failure would repeat for every remaining batch
        for scan, m in batch:
            if m.key in answered:
                ledger.record_extraction(scan.account, m, answered[m.key])
                ledger.mark_seen(scan.account, scan.mailbox, scan.uidvalidity, m.uid)
                done += 1
            elif ledger.record_failure(scan.account, m, "模型漏答了这封"):
                ledger.mark_seen(scan.account, scan.mailbox, scan.uidvalidity, m.uid)
    return done, None


def _advance_cursors(ledger: Ledger, scans: list[Scan]) -> None:
    for s in scans:
        last = None
        for uid in s.uids:
            if not ledger.seen(s.account, s.mailbox, s.uidvalidity, uid):
                break
            last = uid
        finished_all = s.complete and last == (s.uids[-1] if s.uids else None)
        if finished_all and s.uidnext > 1:  # some servers omit UIDNEXT; never fall back to 0
            last = max(last or 0, s.uidnext - 1)
        if last is None:
            continue
        cur = ledger.cursor(s.account, s.mailbox)
        if cur and cur[0] == s.uidvalidity:
            last = max(last, cur[1])  # a --since rescan must not move the cursor back
        ledger.set_cursor(s.account, s.mailbox, s.uidvalidity, last)


def _start_repr(start) -> str:
    return start.astimezone(timezone.utc).isoformat() if isinstance(start, datetime) else start.isoformat()


def _apply(cfg, ledger: Ledger, now, dry_run, caldav_factory, secret, res: Result) -> None:
    pending = ledger.pending()
    if not pending:
        return
    dav = None
    if not dry_run:
        cal = cfg["calendar"]
        password = secret(keychain.CALENDAR_SERVICE, cal["username"])
        if not password:
            raise config.ConfigError("钥匙串里没有日历密码，运行 mailminder account password 补上")
        dav = caldav_factory(cal["url"], cal["username"], password)
    for p in pending:
        for raw in p.events:
            planned = plan(raw, p.message, cfg["timezone"], now)
            if isinstance(planned, str):
                res.skipped.append((p.message.subject, str(raw.get("title", "")), planned))
                continue
            existing = ledger.event(planned.uid)
            if planned.status == "cancelled":
                if existing is None or existing["status"] == "cancelled":
                    res.skipped.append((p.message.subject, planned.event.title, "没有对应的已建日程"))
                    continue
                res.planned.append((p.message.subject, planned, "标记取消"))
                if not dry_run:
                    dav.put_event(existing["href"], to_ics(planned.event), create_only=False)
                    ledger.save_event(planned.uid, existing["href"], planned.event.title,
                                      _start_repr(planned.event.start), "cancelled", p.msg_key)
                    res.cancelled += 1
                continue
            if existing is not None:  # already written — or deleted by the user, who meant it
                res.planned.append((p.message.subject, planned, "已存在"))
                res.duplicates += 1
                continue
            res.planned.append((p.message.subject, planned, "新建"))
            if dry_run:
                continue
            href = cfg["calendar"]["calendar_url"] + planned.uid + ".ics"
            status, _ = dav.put_event(href, to_ics(planned.event), create_only=True)
            if status == 412:
                res.duplicates += 1
            else:
                res.created += 1
            ledger.save_event(planned.uid, href, planned.event.title, _start_repr(planned.event.start),
                              "confirmed", p.msg_key)
        if not dry_run:
            ledger.mark_applied(p.msg_key)


def _alert(cfg, ledger: Ledger, error: str, now: datetime, caldav_factory, secret) -> None:
    """After repeated failures put one alert per day on the calendar, the channel that reaches the phone."""
    runs = ledger.recent_runs(ALERT_AFTER_FAILED_RUNS)
    if len(runs) < ALERT_AFTER_FAILED_RUNS or any(r["ok"] or r["dry_run"] for r in runs):
        return
    today = now.astimezone(ZoneInfo(cfg["timezone"])).date().isoformat()
    if ledger.get_meta("last_alert") == today:
        return
    cal = cfg["calendar"]
    ev = Event(uid=f"mailminder-alert-{today}", title="Mailminder 出错了：邮件提醒暂停中",
               start=now + timedelta(minutes=5), alarms=[timedelta(0)],
               description=f"{error}\n\n在 Mac 上运行 mailminder doctor 查看原因。")
    try:
        dav = caldav_factory(cal["url"], cal["username"], secret(keychain.CALENDAR_SERVICE, cal["username"]) or "")
        dav.put_event(cal["calendar_url"] + ev.uid + ".ics", to_ics(ev), create_only=True)
        ledger.set_meta("last_alert", today)
    except (CalDAVError, OSError):
        pass  # the calendar may be what is broken; the run log already carries the error


def run(cfg: dict, *, dry_run: bool = False, since_days: int | None = None, limit: int | None = None,
        now: datetime | None = None, mailbox_factory=IMAPMailbox, caldav_factory=CalDAV,
        extractor=extract, secret=keychain.get, log=print) -> Result:
    now = now or datetime.now(timezone.utc)
    res = Result()
    with run_lock():
        ledger = Ledger(config.state_dir() / "state.db")
        run_id = ledger.start_run(dry_run)
        try:
            scans, fresh = _scan(cfg, ledger, since_days, limit or int(cfg["max_per_run"]), mailbox_factory, secret)
            res.fetched = len(fresh)
            res.extracted, res.error = _extract(cfg, ledger, fresh, now, extractor, log)
            _advance_cursors(ledger, scans)
            _apply(cfg, ledger, now, dry_run, caldav_factory, secret, res)
        except (MailError, CalDAVError, config.ConfigError, OSError) as e:
            res.error = str(e)
        except Exception as e:
            res.error = f"意外错误：{type(e).__name__}: {e}"
            raise
        finally:
            stats = {"fetched": res.fetched, "extracted": res.extracted, "created": res.created,
                     "cancelled": res.cancelled}
            ledger.finish_run(run_id, res.error is None, stats, res.error)
            if res.error and not dry_run:
                _alert(cfg, ledger, res.error, now, caldav_factory, secret)
            ledger.close()
    return res


def upcoming(ledger: Ledger, now: datetime, zone: str) -> list:
    today = now.astimezone(ZoneInfo(zone)).date()
    out = []
    for r in ledger.events():
        start = datetime.fromisoformat(r["start"]) if "T" in r["start"] else date.fromisoformat(r["start"])
        if (start >= now if isinstance(start, datetime) else start >= today):
            out.append((start, r))
    return out
