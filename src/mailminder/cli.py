"""mailminder command line: setup wizard and day-to-day commands."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import tomllib
import uuid
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__, accounts, config, doctor, icloud, keychain, launchd, oauth, pipeline
from .caldav import CalDAV, CalDAVError
from .extract import ping
from .ics import Event, to_ics
from .ledger import Ledger
from .mailbox import IMAPMailbox, MailError

HARNESS_NAMES = {"claude": "Claude Code", "codex": "Codex"}
HARNESS_CANDIDATES = {
    "claude": ["~/.local/bin/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"],
    "codex": ["/opt/homebrew/bin/codex", "/usr/local/bin/codex", "~/.local/bin/codex"],
}
CLAUDE_MODELS = [("opus", "Opus：默认，读得最准"), ("sonnet", "Sonnet：更快、更省额度"),
                 ("haiku", "Haiku：最省"), ("fable", "Fable：最强，额度最紧")]
MAIL_PRESETS = {  # kind: (label, IMAP host or None = from the address, where to get the password)
    "gmail": ("Gmail", "imap.gmail.com",
              "Gmail 要用「应用专用密码」：打开 https://myaccount.google.com/apppasswords 生成（Google 账号需先开两步验证）。"),
    "qq": ("QQ 邮箱", "imap.qq.com", "QQ 邮箱要用「授权码」：网页版 设置 → 账号 → 开启 IMAP/SMTP 服务 → 生成授权码。"),
    "163": ("网易邮箱（163/126/yeah）", None,
            "网易邮箱要用「授权码」：网页版 设置 → POP3/SMTP/IMAP → 开启 IMAP 服务 → 新增授权密码。"),
}
WEEKDAYS = "一二三四五六日"


# --- terminal helpers -------------------------------------------------------

def say(msg: str = "") -> None:
    print(msg, flush=True)


def ask(prompt: str, default: str = "") -> str:
    hint = f"（回车 = {default}）" if default else ""
    try:
        answer = input(f"{prompt}{hint}：").strip()
    except EOFError:
        answer = ""
    return answer or default


def ask_int(prompt: str, default: int) -> int:
    while True:
        raw = ask(prompt, str(default))
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        say("  请输入正整数")


def ask_yes(prompt: str, default: bool = True) -> bool:
    answer = ask(f"{prompt} [{'Y/n' if default else 'y/N'}]").lower()
    return default if not answer else answer in ("y", "yes", "是", "好")


def choose(prompt: str, options: list[tuple[str, str]], default: int = 0) -> str:
    say(prompt)
    for i, (_, label) in enumerate(options, 1):
        say(f"  {i}) {label}")
    while True:
        answer = ask("选一个", str(default + 1))
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]
        say("  请输入上面的序号")


def ask_secret(prompt: str) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", getpass.GetPassWarning)  # scripted setup pipes the secret in
        try:
            return "".join(getpass.getpass(f"{prompt}（输入时不显示）：").split())
        except EOFError:
            return ""


def check_lines(checks: list[dict]) -> None:
    for c in checks:
        say(f"  {'✓' if c['ok'] else '✗'} {c['name']}：{c['detail']}")


def when(start, zone: str) -> str:
    if isinstance(start, datetime):
        local = start.astimezone(ZoneInfo(zone))
        return f"{local:%m-%d} 周{WEEKDAYS[local.weekday()]} {local:%H:%M}"
    return f"{start:%m-%d} 周{WEEKDAYS[start.weekday()]} 全天"


def show_plan(res: pipeline.Result, cfg: dict) -> None:
    marks = {"新建": "+", "已存在": "=", "标记取消": "×"}
    for subject, planned, action in res.planned:
        say(f"  {marks[action]} {when(planned.event.start, cfg['timezone']):16} {planned.event.title}"
            f"   ← 《{subject[:40]}》{'' if action == '新建' else f'（{action}）'}")
    for subject, title, reason in res.skipped:
        if reason != "已经过去":
            say(f"  - 跳过「{title}」：{reason}  ← 《{subject[:40]}》")


# --- the AI harness -----------------------------------------------------------

def find_harness(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for candidate in HARNESS_CANDIDATES[name]:
        path = os.path.expanduser(candidate)
        if os.path.exists(path):
            return path
    return None


def codex_default_model() -> str:
    path = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    try:
        return str(tomllib.loads(path.read_text()).get("model") or "")
    except (OSError, tomllib.TOMLDecodeError):
        return ""


def default_model(harness: str) -> str:
    return "opus" if harness == "claude" else codex_default_model()


def ping_until_ok(ai: dict) -> None:
    while True:
        say("试跑一次，确认已登录、模型能用……")
        ok, detail = ping(ai, str(config.work_dir()))
        if ok:
            say(f"  ✓ 可以用（{detail}）")
            return
        say(f"  ✗ {detail}")
        if ai["harness"] == "codex" and ask_yes("现在运行 codex login 登录？"):
            subprocess.run([ai["path"], "login"])
            continue
        if ai["harness"] == "claude":
            say("  如果是没登录：另开一个终端运行 claude，输入 /login 登录后回来。")
        if not ask_yes("重试？"):
            raise SystemExit(1)


def setup_ai(cfg: dict) -> None:
    found = {n: p for n in ("claude", "codex") if (p := find_harness(n))}
    if not found:
        say("没找到 Claude Code 或 Codex，先装其中一个再来：")
        say("  Claude Code：https://claude.com/claude-code")
        say("  Codex：npm install -g @openai/codex")
        raise SystemExit(1)
    options = [(n, f"{HARNESS_NAMES[n]}（{p}）") for n, p in found.items()]
    names = [n for n, _ in options]
    harness = choose("用哪个 AI 来读邮件？", options,
                     names.index(cfg["ai"]["harness"]) if cfg["ai"]["harness"] in names else 0)
    if harness == "claude":
        model = choose("选模型：", CLAUDE_MODELS + [("", "其他（手动输入模型名）")])
        model = model or ask("模型名")
    else:
        model = ask("Codex 用哪个模型", codex_default_model())
    ai = {**cfg["ai"], "harness": harness, "path": found[harness], "model": model}
    ping_until_ok(ai)
    cfg["ai"] = ai


# --- mail -------------------------------------------------------------------------

def try_login(host: str, port: int, usernames: list[str], password: str) -> tuple[str | None, str]:
    error = ""
    for user in usernames:
        try:
            with IMAPMailbox(host, port, user, password) as mb:
                mb.select("INBOX")
            return user, ""
        except MailError as e:
            error = str(e)
    return None, error


OUTLOOK_APP_HELP = """Outlook 要用微软账号授权登录（微软已不接受密码），需要先在微软注册一个应用拿到「应用 ID」（免费、一次性，约 5 分钟）：
  1) 打开 https://entra.microsoft.com 登录（个人微软账号若提示没有目录，先在 https://azure.microsoft.com/free 开一个免费 Azure 账户）
  2) 应用注册 → 新注册：名称填 Mailminder；支持的账户类型选「任何组织目录中的帐户和个人 Microsoft 帐户」；
     重定向 URI 不用填
  3) 注册后进「身份验证」，把「允许公共客户端流」设为「是」，保存
  4) 在「概述」页复制「应用程序(客户端) ID」
详细图文步骤见 README 的「Outlook」一节。"""


OUTLOOK_DOMAINS = ("@outlook.com", "@hotmail.com", "@live.com", "@msn.com", "@outlook.cn",
                   "@hotmail.co.uk", "@live.cn", "@outlook.jp")


def microsoft_sign_in(client_id: str, tenant: str) -> tuple[dict, str]:
    """Device-code sign-in; returns (token response, tenant that worked)."""
    while True:
        try:
            code = oauth.start_device_login(client_id, tenant)
            say(f"用浏览器打开 {oauth.DEVICE_PAGE} ，输入代码：{code.user_code}")
            say("（手机上打开也行，15 分钟内有效。用要读的那个邮箱账号登录，点「接受」后回到这里，这边会自动继续）")
            tokens = oauth.finish_device_login(client_id, tenant, code)
            say("  ✓ 微软账号已授权")
            return tokens, tenant
        except oauth.OAuthError as e:
            say(f"  ✗ {e}")
            if "AADSTS9002331" in str(e) and tenant != "consumers":
                tenant = "consumers"  # app registered for personal accounts only
                continue
            if "AADSTS50194" in str(e):
                tenant = ask("这个应用是单租户的，填它所在目录的「目录(租户) ID」")
                continue
            if not ask_yes("重试？"):
                raise SystemExit(1) from None


def setup_outlook(cfg: dict) -> tuple[dict, None]:
    known = next((a for a in cfg["accounts"] if accounts.is_microsoft(a)), {})
    client_id = known.get("client_id") or oauth.DEFAULT_CLIENT_ID
    if not client_id:
        say(OUTLOOK_APP_HELP)
        client_id = ask("粘贴应用(客户端) ID")
    tokens, tenant = microsoft_sign_in(client_id, known.get("tenant") or "common")
    address = oauth.signed_in_address(tokens)
    if not address or not address.lower().endswith(OUTLOOK_DOMAINS):
        # A Microsoft account opened with another provider's address (e.g. Gmail) signs in with
        # that address, but Outlook's IMAP wants the mailbox's own @outlook.com/@hotmail.com one.
        address = ask("你的 Outlook 邮箱地址（@outlook.com / @hotmail.com / @live.com，公司邮箱填公司地址）",
                      address or "")
    while True:
        say(f"正在登录邮箱 {address}……")
        try:
            with IMAPMailbox(oauth.IMAP_HOST, 993, address, "", oauth_token=tokens["access_token"]) as mb:
                mb.select("INBOX")
            break
        except MailError as e:
            say(f"  ✗ {e}")
            say("  常见原因：① 地址要填邮箱本身的地址（不是注册微软账号用的其他邮箱）；"
                "② Outlook 网页版 设置 → 邮件 → 转发和 IMAP →「允许设备和应用使用 IMAP」没打开；"
                "③ 公司邮箱被管理员关了 IMAP。")
            retry = ask("换个地址再试（直接回车 = 放弃）")
            if not retry:
                raise SystemExit(1) from None
            address = retry
    account = {"name": "Outlook", "host": oauth.IMAP_HOST, "port": 993, "username": address,
               "mailboxes": ["INBOX"], "auth": accounts.MICROSOFT, "client_id": client_id, "tenant": tenant}
    key = config.mail_secret_account(account)
    keychain.put(keychain.MAIL_SERVICE, key, tokens["refresh_token"], label=f"Mailminder 邮箱 {address}")
    cfg["accounts"] = [a for a in cfg["accounts"] if config.mail_secret_account(a) != key] + [account]
    say(f"  ✓ 已登录 {address}；钥匙串里存的是微软给的授权，不是你的密码")
    return account, None


def setup_mail(cfg: dict) -> tuple[dict, str | None]:
    det = icloud.detect()
    icloud_label = f"iCloud 邮箱 {det.mail_address}（这台 Mac 登录的账户）" if det and det.mail_address else "iCloud 邮箱"
    options = [("icloud", icloud_label), ("outlook", "Outlook / Hotmail / Microsoft 365（用微软账号授权）")]
    options += [(k, v[0]) for k, v in MAIL_PRESETS.items()] + [("imap", "其他邮箱（手动填 IMAP 服务器）")]
    kind = choose("读哪个邮箱？", options)
    if kind == "outlook":
        return setup_outlook(cfg)
    port = 993
    if kind == "icloud":
        name, host = "iCloud", (det.imap_host if det and det.imap_host else icloud.FALLBACK_IMAP)
        if det and det.mail_address:
            usernames = det.imap_usernames()
        else:
            address = ask("iCloud 邮箱地址（如 name@icloud.com）")
            usernames = [address.split("@")[0], address]
        say(icloud.APP_PASSWORD_HELP)
    elif kind in MAIL_PRESETS:
        name, host, help_text = MAIL_PRESETS[kind]
        address = ask(f"{name}地址")
        host = host or f"imap.{address.split('@')[-1]}"
        usernames = [address]
        say(help_text)
    else:
        host = ask("IMAP 服务器（如 imap.example.com）")
        port = ask_int("端口", 993)
        usernames = [ask("登录用户名（通常就是邮箱地址）")]
        name = host
    while True:
        password = ask_secret("粘贴密码")
        if not password:
            continue
        say("正在登录……")
        user, error = try_login(host, port, usernames, password)
        if user:
            break
        say(f"  ✗ {error}")
        if not ask_yes("重新输入？"):
            raise SystemExit(1)
    account = {"name": name, "host": host, "port": port, "username": user, "mailboxes": ["INBOX"]}
    secret_account = config.mail_secret_account(account)
    keychain.put(keychain.MAIL_SERVICE, secret_account, password, label=f"Mailminder 邮箱 {user}")
    cfg["accounts"] = [a for a in cfg["accounts"] if config.mail_secret_account(a) != secret_account] + [account]
    say(f"  ✓ 已登录 {user}，密码存进了钥匙串")
    return account, password


# --- calendar -----------------------------------------------------------------------

def connect_calendar(urls: list[str], username: str, password: str) -> tuple[CalDAV, str, str]:
    error = None
    for url in urls:
        try:
            dav = CalDAV(url, username, password)
            return dav, dav.discover_home(), url
        except (CalDAVError, OSError) as e:
            error = e
    raise CalDAVError(str(error))


def write_probe(dav: CalDAV, calendar_url: str) -> None:
    uid = f"mailminder-probe-{uuid.uuid4().hex}"
    href = f"{calendar_url}{uid}.ics"
    ev = Event(uid=uid, title="Mailminder 写入测试（会自动删除）",
               start=datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30))
    dav.put_event(href, to_ics(ev), create_only=True)
    dav.delete(href)


def setup_calendar(cfg: dict, icloud_password: str | None) -> None:
    det = icloud.detect()
    kind = choose("日程写到哪个日历？", [("icloud", "iCloud 日历（手机上的「日历」会自动同步）"),
                                  ("caldav", "其他 CalDAV 日历（手动填地址）")])
    if kind == "icloud":
        username = det.apple_id if det else ask("Apple 账户（登录 iCloud 用的邮箱）")
        urls = [det.caldav_url] if det and det.caldav_url else list(icloud.FALLBACK_CALDAV)
        password = icloud_password if icloud_password and ask_yes("iCloud 日历用刚才那个 App 专用密码？") else ""
    else:
        urls, username, password = [ask("CalDAV 地址")], ask("用户名"), ""
    while True:
        if not password:
            if kind == "icloud":
                say(icloud.APP_PASSWORD_HELP)
            password = ask_secret("粘贴日历密码")
        say("正在连接日历……")
        try:
            dav, home, url = connect_calendar(urls, username, password)
            break
        except CalDAVError as e:
            say(f"  ✗ {e}")
            password = ""
            if not ask_yes("重试？"):
                raise SystemExit(1) from None
    calendars = [c for c in dav.calendars(home) if "VEVENT" in c.components and c.writable]
    name = cfg["calendar"].get("calendar_name") or "邮件提醒"
    existing = next((c for c in calendars if c.name == name), None)
    options = [("dedicated", f"{'用已有的' if existing else '新建'}专用日历「{name}」（推荐：不想要时整个隐藏或删掉就行）")]
    options += [(c.url, f"写进已有日历「{c.name}」") for c in calendars if c is not existing]
    pick = choose("选日历：", options)
    if pick == "dedicated":
        target = existing or dav.make_calendar(home, name)
    else:
        target = next(c for c in calendars if c.url == pick)
    say("写一条测试日程再删掉……")
    write_probe(dav, target.url)
    keychain.put(keychain.CALENDAR_SERVICE, username, password, label=f"Mailminder 日历 {username}")
    cfg["calendar"] = {"url": url, "username": username, "calendar_url": target.url, "calendar_name": target.name}
    say(f"  ✓ 日历「{target.name}」可写，密码存进了钥匙串")


# --- first run and the background job ------------------------------------------------

def first_run(cfg: dict) -> None:
    say(f"让模型读最近 {cfg['lookback_days']} 天的邮件，先只看不写……")
    res = pipeline.run(cfg, dry_run=True, log=lambda m: say(f"  {m}"))
    if res.error:
        say(f"  ✗ {res.error}")
        return
    new = [p for p in res.planned if p[2] == "新建"]
    show_plan(res, cfg)
    if not new:
        say("  这几天的邮件里没有需要提醒的事。")
        return
    if ask_yes(f"把这 {len(new)} 条写进日历「{cfg['calendar']['calendar_name']}」？"):
        res = pipeline.run(cfg, log=lambda m: say(f"  {m}"))
        say(f"  ✗ {res.error}" if res.error else f"  ✓ 已写入 {res.created} 条")
    else:
        ledger = Ledger(config.state_dir() / "state.db")
        ledger.discard_pending()
        ledger.close()
        say("  这批不写了，之后只处理新邮件。")


def install_agent(cfg: dict) -> bool:
    launchd.install(cfg)
    say(f"  ✓ 后台任务已安装：每 {cfg['interval_minutes']} 分钟查一次，开机登录后自动运行")
    say("按后台任务的条件自检一遍（无终端、精简环境、自己读钥匙串），提前把权限问题暴露出来……")
    checks = launchd.selftest(cfg)
    check_lines(checks)
    return all(c["ok"] for c in checks)


# --- commands -----------------------------------------------------------------------------

def cmd_init(args) -> int:
    say(f"Mailminder {__version__}：把邮件里要记住的时间，变成日历里带提醒的日程。")
    say("下面 5 步，每一步之后都能用命令单独改。")
    try:
        cfg = config.load()
    except config.ConfigError:
        cfg = config.defaults()
    say("\n[1/5] AI")
    setup_ai(cfg)
    config.save(cfg)
    say("\n[2/5] 邮箱")
    account, mail_password = setup_mail(cfg)
    config.save(cfg)
    say("\n[3/5] 日历")
    setup_calendar(cfg, mail_password if account["name"] == "iCloud" else None)
    config.save(cfg)
    say("\n[4/5] 时区和频率")
    while True:
        try:
            config.set_value(cfg, "timezone", ask("你所在的时区（邮件没写时区时按它算）", cfg["timezone"]))
            break
        except config.ConfigError as e:
            say(f"  {e}")
    cfg["interval_minutes"] = ask_int("每隔几分钟查一次新邮件", cfg["interval_minutes"])
    cfg["lookback_days"] = ask_int("第一次往回看几天的邮件", cfg["lookback_days"])
    config.save(cfg)
    say("\n[5/5] 试运行")
    first_run(cfg)
    ok = True
    if ask_yes("安装后台任务，让它自动运行？"):
        ok = install_agent(cfg)
    say("\n设置完成。" if ok else "\n设置完成，但自检有问题，按上面的提示处理后运行 mailminder doctor。")
    say("常用命令：mailminder status（看状态） · mailminder model（换模型） · mailminder account（管理邮箱和密码）")
    return 0 if ok else 1


def cmd_run(args) -> int:
    cfg = config.load()
    stamp = (lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S ")) if args.scheduled else (lambda: "")
    log = lambda m: say(stamp() + m)  # noqa: E731
    if args.scheduled:
        trim_log(config.state_dir() / "logs" / "agent.log")
    try:
        res = pipeline.run(cfg, dry_run=args.dry_run, since_days=args.since, limit=args.limit, log=log)
    except pipeline.RunLocked as e:
        log(str(e))
        return 0
    if not args.scheduled:
        show_plan(res, cfg)
    new = sum(1 for p in res.planned if p[2] == "新建")
    if args.dry_run:
        log(f"新邮件 {res.fetched} 封；会新建 {new} 条日程（试运行，没写入）")
    else:
        log(f"新邮件 {res.fetched} 封，模型读了 {res.extracted} 封；新建 {res.created} 条，标记取消 {res.cancelled} 条")
    if res.error:
        log(f"出错：{res.error}")
    return 1 if res.error else 0


def trim_log(path: Path, limit: int = 2_000_000) -> None:
    if path.exists() and path.stat().st_size > limit:
        data = path.read_bytes()[-limit // 4:]
        path.write_bytes(data[data.find(b"\n") + 1:])


def cmd_status(args) -> int:
    cfg = config.load()
    ai, cal = cfg["ai"], cfg["calendar"]
    say(f"Mailminder {__version__}")
    say(f"AI：{HARNESS_NAMES.get(ai['harness'], ai['harness'])} / {ai['model'] or '默认模型'}（effort {ai['effort'] or '默认'}）")
    for a in cfg["accounts"]:
        has = "✓" if keychain.get(keychain.MAIL_SERVICE, config.mail_secret_account(a)) else "✗ 缺密码"
        say(f"邮箱：{a['name']} {a['username']}（{', '.join(a.get('mailboxes') or ['INBOX'])}）{has}")
    say(f"日历：「{cal['calendar_name']}」{'✓' if keychain.get(keychain.CALENDAR_SERVICE, cal['username']) else '✗ 缺密码'}")
    say(f"时区 {cfg['timezone']} · 每 {cfg['interval_minutes']} 分钟 · 首次回看 {cfg['lookback_days']} 天")
    st = launchd.status()
    if st is None:
        say("后台任务：没装（运行 mailminder agent install）")
    else:
        say(f"后台任务：已装，{'正在运行' if st.get('state') == 'running' else '等下一次'}"
            f"，上次退出码 {st.get('last exit code', '—')}")
    ledger = Ledger(config.state_dir() / "state.db")
    runs = ledger.recent_runs(5)
    if runs:
        say("最近运行：")
        for r in runs:
            t = datetime.fromisoformat(r["started_at"]).astimezone(ZoneInfo(cfg["timezone"]))
            if not r["ok"]:
                detail = f"出错：{(r['error'] or '没跑完')[:80]}"
            elif r["dry_run"]:
                detail = f"试运行，读 {r['extracted'] or 0} 封"
            else:
                detail = f"读 {r['extracted'] or 0} 封，新建 {r['created'] or 0} 条"
            say(f"  {t:%m-%d %H:%M} {'✓' if r['ok'] else '✗'} {detail}")
    items = pipeline.upcoming(ledger, datetime.now(timezone.utc), cfg["timezone"])
    if items:
        say("接下来（Mailminder 建的）：")
        for start, r in items[:10]:
            say(f"  {when(start, cfg['timezone']):16} {r['title']}")
    ledger.close()
    return 0


def cmd_doctor(args) -> int:
    cfg = config.load()
    checks = doctor.run_checks(cfg, write_test=args.write_test)
    if not args.json:
        st = launchd.status()
        checks.append({"name": "后台任务", "ok": st is not None,
                       "detail": f"已装，上次退出码 {st.get('last exit code', '—')}" if st else "没装（mailminder agent install）"})
        check_lines(checks)
    else:
        Path(args.json).write_text(json.dumps(checks, ensure_ascii=False))
    return 0 if all(c["ok"] for c in checks) else 1


def cmd_model(args) -> int:
    cfg = config.load()
    ai = dict(cfg["ai"])
    if not (args.name or args.harness or args.effort):
        say(f"现在：{HARNESS_NAMES.get(ai['harness'], ai['harness'])} / {ai['model'] or '默认模型'}，effort {ai['effort'] or '默认'}")
        say("Claude Code 可选：opus · sonnet · haiku · fable，或完整模型 ID")
        say(f"Codex 可选：任意 Codex 支持的模型名（你的 Codex 默认是 {codex_default_model() or '未设置'}）")
        say("改法：mailminder model sonnet  ·  mailminder model --harness codex gpt-6-astra  ·  mailminder model --effort low")
        return 0
    harness_changed = bool(args.harness and args.harness != ai["harness"])
    if harness_changed:
        path = find_harness(args.harness)
        if not path:
            say(f"没找到 {HARNESS_NAMES[args.harness]}")
            return 1
        ai.update(harness=args.harness, path=path, model=args.name or default_model(args.harness))
    if args.name:
        ai["model"] = args.name
    if args.effort:
        ai["effort"] = config.SETTABLE["effort"][2](args.effort)
    if not args.no_check:
        say("试跑一次……")
        ok, detail = ping(ai, str(config.work_dir()))
        if not ok:
            say(f"  ✗ {detail}（没有保存）")
            return 1
        say(f"  ✓ 可以用（{detail}）")
    cfg["ai"] = ai
    config.save(cfg)
    if harness_changed and launchd.status() is not None:
        launchd.install(cfg)  # the job's PATH depends on where the CLI lives
    say(f"已改为 {HARNESS_NAMES[ai['harness']]} / {ai['model'] or '默认模型'}，下一次运行生效")
    return 0


def _account_label(a: dict) -> str:
    return f"{a['name']} {a['username']}"


def cmd_account(args) -> int:
    cfg = config.load()
    if args.action in (None, "list"):
        for i, a in enumerate(cfg["accounts"], 1):
            has = keychain.get(keychain.MAIL_SERVICE, config.mail_secret_account(a))
            kind = "微软授权" if accounts.is_microsoft(a) else "密码"
            say(f"  {i}) {_account_label(a)} · {a['host']} · {', '.join(a.get('mailboxes') or ['INBOX'])}"
                f" · {kind} {'✓' if has else '缺'}")
        cal = cfg["calendar"]
        say(f"  日历：{cal['username']} →「{cal['calendar_name']}」")
        say("命令：mailminder account add · remove · password · folders")
        return 0
    if args.action == "add":
        setup_mail(cfg)
        config.save(cfg)
        first_run(cfg)  # the new mailbox's recent mail: show first, write only after a yes
        return 0
    if args.action == "remove":
        options = [(config.mail_secret_account(a), _account_label(a)) for a in cfg["accounts"]]
        if not options:
            say("没有邮箱")
            return 1
        key = choose("删掉哪个邮箱？", options)
        cfg["accounts"] = [a for a in cfg["accounts"] if config.mail_secret_account(a) != key]
        keychain.delete(keychain.MAIL_SERVICE, key)
        config.save(cfg)
        say("  ✓ 已删掉，钥匙串里的密码也删了")
        return 0
    if args.action == "folders":
        options = [(config.mail_secret_account(a), _account_label(a)) for a in cfg["accounts"]]
        key = choose("哪个邮箱？", options)
        acct = next(a for a in cfg["accounts"] if config.mail_secret_account(a) == key)
        raw = ask("要读的文件夹，逗号分隔", ", ".join(acct.get("mailboxes") or ["INBOX"]))
        acct["mailboxes"] = [f.strip() for f in raw.replace("，", ",").split(",") if f.strip()]
        config.save(cfg)
        say(f"  ✓ 现在读：{', '.join(acct['mailboxes'])}")
        return 0
    # password: a new app password for a mailbox or the calendar
    options = [(config.mail_secret_account(a), f"邮箱 {_account_label(a)}") for a in cfg["accounts"]]
    options.append(("calendar", f"日历 {cfg['calendar']['username']}"))
    key = choose("更新哪个密码？", options)
    acct = next((a for a in cfg["accounts"] if config.mail_secret_account(a) == key), None)
    if acct and accounts.is_microsoft(acct):
        tokens, acct["tenant"] = microsoft_sign_in(acct["client_id"], acct.get("tenant") or "common")
        keychain.put(keychain.MAIL_SERVICE, key, tokens["refresh_token"], label=f"Mailminder 邮箱 {acct['username']}")
        config.save(cfg)
        say("  ✓ 已重新授权")
        return 0
    password = ask_secret("新密码")
    if key == "calendar":
        cal = cfg["calendar"]
        CalDAV(cal["url"], cal["username"], password).discover_home()
        keychain.put(keychain.CALENDAR_SERVICE, cal["username"], password, label=f"Mailminder 日历 {cal['username']}")
    else:
        acct = next(a for a in cfg["accounts"] if config.mail_secret_account(a) == key)
        user, error = try_login(acct["host"], int(acct.get("port", 993)), [acct["username"]], password)
        if not user:
            say(f"  ✗ {error}（没有保存）")
            return 1
        keychain.put(keychain.MAIL_SERVICE, key, password, label=f"Mailminder 邮箱 {acct['username']}")
    say("  ✓ 验证通过，已存进钥匙串")
    return 0


def cmd_config(args) -> int:
    cfg = config.load()
    if args.action in (None, "show"):
        say(f"配置文件：{config.config_path()}")
        say(f"运行记录：{config.state_dir()}")
        say(config.dumps(cfg))
        say(f"可用 mailminder config set 修改：{', '.join(config.SETTABLE)}")
        return 0
    config.set_value(cfg, args.key, args.value)
    config.save(cfg)
    if args.key == "interval" and launchd.status() is not None:
        launchd.install(cfg)
    say(f"  ✓ {args.key} = {args.value}")
    return 0


def cmd_agent(args) -> int:
    cfg = config.load()
    if args.action == "install":
        return 0 if install_agent(cfg) else 1
    if args.action == "uninstall":
        say("  ✓ 后台任务已卸载" if launchd.uninstall() else "  后台任务本来就没装")
        return 0
    checks = launchd.selftest(cfg)
    check_lines(checks)
    return 0 if all(c["ok"] for c in checks) else 1


def cmd_delete_all(args) -> int:
    cfg = config.load()
    ledger = Ledger(config.state_dir() / "state.db")
    rows = ledger.events()
    if not rows:
        say("Mailminder 还没建过日程")
        return 0
    if not args.yes and not ask_yes(f"删掉 Mailminder 建的全部 {len(rows)} 条日程？", default=False):
        return 0
    cal = cfg["calendar"]
    dav = CalDAV(cal["url"], cal["username"], keychain.get(keychain.CALENDAR_SERVICE, cal["username"]) or "")
    for r in rows:
        dav.delete(r["href"])
        ledger.forget_event(r["uid"])
    ledger.close()
    say(f"  ✓ 删掉了 {len(rows)} 条")
    return 0


def cmd_uninstall(args) -> int:
    launchd.uninstall()
    say("  ✓ 后台任务已卸载")
    if args.purge:
        try:
            cfg = config.load()
        except config.ConfigError:
            cfg = None
        if cfg:
            for a in cfg["accounts"]:
                keychain.delete(keychain.MAIL_SERVICE, config.mail_secret_account(a))
            if cfg["calendar"]["username"]:
                keychain.delete(keychain.CALENDAR_SERVICE, cfg["calendar"]["username"])
            say("  ✓ 钥匙串里的密码已删")
            say(f"  日历「{cfg['calendar']['calendar_name']}」和里面的日程留着；不要了就在「日历」App 里删掉。")
        config.config_path().unlink(missing_ok=True)
        shutil.rmtree(config.state_dir(), ignore_errors=True)
        say("  ✓ 配置和运行记录已删")
    say("程序本身用 uv tool uninstall mailminder 删除。")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mailminder", description="把邮件里要记住的时间，变成日历里带提醒的日程")
    p.add_argument("--version", action="version", version=f"mailminder {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="命令")

    sub.add_parser("init", help="设置向导：选 AI 和模型、邮箱、日历，装后台任务").set_defaults(func=cmd_init)
    r = sub.add_parser("run", help="现在查一次新邮件")
    r.add_argument("--dry-run", action="store_true", help="只看不写")
    r.add_argument("--since", type=int, metavar="天", help="重新看最近 N 天（已读过的不会重复花模型）")
    r.add_argument("--limit", type=int, metavar="封", help="这次最多读几封")
    r.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)
    r.set_defaults(func=cmd_run)
    sub.add_parser("status", help="看设置、后台任务、最近运行和接下来的日程").set_defaults(func=cmd_status)
    d = sub.add_parser("doctor", help="逐项检查：模型、邮箱、日历、后台任务")
    d.add_argument("--write-test", action="store_true", help="往日历写一条测试日程再删掉")
    d.add_argument("--json", metavar="文件", help=argparse.SUPPRESS)
    d.set_defaults(func=cmd_doctor)
    m = sub.add_parser("model", help="查看或更换 AI 底座和模型")
    m.add_argument("name", nargs="?", help="模型名，如 opus / sonnet / gpt-6-astra")
    m.add_argument("--harness", choices=sorted(HARNESS_NAMES), help="换底座：claude 或 codex")
    m.add_argument("--effort", choices=config.EFFORTS, help="推理强度")
    m.add_argument("--no-check", action="store_true", help="不试跑直接保存")
    m.set_defaults(func=cmd_model)
    a = sub.add_parser("account", help="管理邮箱账号、文件夹和 App 专用密码")
    a.add_argument("action", nargs="?", choices=["list", "add", "remove", "password", "folders"])
    a.set_defaults(func=cmd_account)
    c = sub.add_parser("config", help="查看或修改设置（时区、频率等）")
    c.add_argument("action", nargs="?", choices=["show", "set"])
    c.add_argument("key", nargs="?")
    c.add_argument("value", nargs="?")
    c.set_defaults(func=cmd_config)
    g = sub.add_parser("agent", help="后台任务：install / uninstall / selftest")
    g.add_argument("action", choices=["install", "uninstall", "selftest"])
    g.set_defaults(func=cmd_agent)
    x = sub.add_parser("delete-all", help="删掉 Mailminder 建的全部日程")
    x.add_argument("--yes", action="store_true")
    x.set_defaults(func=cmd_delete_all)
    u = sub.add_parser("uninstall", help="卸载后台任务；--purge 连密码、配置、记录一起删")
    u.add_argument("--purge", action="store_true")
    u.set_defaults(func=cmd_uninstall)

    args = p.parse_args(argv)
    if getattr(args, "action", None) == "set" and not (args.key and args.value):
        p.error("用法：mailminder config set <key> <value>")
    try:
        return args.func(args) or 0
    except (config.ConfigError, CalDAVError, MailError, keychain.KeychainError, oauth.OAuthError) as e:
        say(str(e))
        return 1
    except KeyboardInterrupt:
        say("\n已取消")
        return 130

