"""Every precondition of a scheduled run, checked in one place.

Used by `mailminder doctor`, by setup, and by the launchd self-test.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from . import accounts, config, keychain
from .caldav import CalDAV
from .extract import ping
from .ics import Event, to_ics


def _check(name: str, fn) -> dict:
    try:
        detail = fn()
        return {"name": name, "ok": True, "detail": detail or ""}
    except Exception as e:  # every failure becomes a readable line, never a traceback
        return {"name": name, "ok": False, "detail": str(e) or type(e).__name__}


def run_checks(cfg: dict, *, write_test: bool = False) -> list[dict]:
    checks = []

    def ai():
        path = cfg["ai"]["path"]
        if not path or not os.path.exists(path):
            raise RuntimeError(f"找不到 {cfg['ai']['harness']}：{path or '没设置'}")
        ok, detail = ping(cfg["ai"], str(config.work_dir()))
        if not ok:
            raise RuntimeError(detail)
        return f"{cfg['ai']['harness']} / {cfg['ai']['model'] or '默认模型'}，试跑 {detail}"

    checks.append(_check("AI 模型", ai))

    for acct in cfg["accounts"]:
        def mail(acct=acct):
            with accounts.open_mailbox(acct) as mb:
                opened = []
                for mbox in acct.get("mailboxes") or ["INBOX"]:
                    mb.select(mbox)
                    opened.append(mbox)
            return f"已登录，能读 {', '.join(opened)}"

        checks.append(_check(f"邮箱 {acct.get('name') or acct['username']}", mail))

    def calendar():
        cal = cfg["calendar"]
        password = keychain.get(keychain.CALENDAR_SERVICE, cal["username"])
        if not password:
            raise RuntimeError("钥匙串里没有日历密码")
        dav = CalDAV(cal["url"], cal["username"], password)
        home = dav.discover_home()
        found = {c.url: c for c in dav.calendars(home)}
        target = found.get(cal["calendar_url"])
        if target is None:
            raise RuntimeError(f"日历「{cal['calendar_name']}」不在了（被删了？）运行 mailminder init 重新选")
        if not target.writable:
            raise RuntimeError(f"日历「{target.name}」是只读的")
        if write_test:
            uid = f"mailminder-probe-{uuid.uuid4().hex}"
            href = f"{target.url}{uid}.ics"
            ev = Event(uid=uid, title="Mailminder 写入测试（会自动删除）",
                       start=datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30))
            dav.put_event(href, to_ics(ev), create_only=True)
            dav.delete(href)
            return f"「{target.name}」可写（已写入并删除一条测试日程）"
        return f"「{target.name}」可写"

    checks.append(_check("日历", calendar))
    return checks
