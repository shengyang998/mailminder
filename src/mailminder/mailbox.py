"""Read-only IMAP access and email → plain text for the model.

Mailboxes are opened with EXAMINE (read-only) and bodies fetched with
BODY.PEEK, so unread mail stays unread.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import imaplib
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.message import Message as _EmailMessage
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

MAX_FETCH_BYTES = 400_000  # text parts come first; don't download attachment tails
MAX_TEXT_CHARS = 8_000
MAX_CALENDAR_CHARS = 3_000
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u034f\u00ad"), None)
_GB = {"gb2312", "gbk", "x-gbk", "gb_2312-80", "euc-cn", "cp936"}


class MailError(RuntimeError):
    pass


@dataclass
class Message:
    mailbox: str
    uid: int
    message_id: str
    date: datetime | None  # as sent, with the sender's UTC offset
    sender: str
    subject: str
    text: str

    @property
    def key(self) -> str:
        """Stable across folder moves; falls back to a content hash."""
        if self.message_id:
            return self.message_id
        blob = f"{self.sender}\n{self.subject}\n{self.date}\n{self.text}".encode()
        return "sha256:" + hashlib.sha256(blob).hexdigest()


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5",
             "h6", "section", "article", "header", "footer", "blockquote", "pre", "hr"}
    SKIP = {"script", "style", "head", "title", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("  ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def tidy(text: str) -> str:
    text = text.translate(_INVISIBLE).replace("\xa0", " ").replace("\u2007", " ")
    lines = (re.sub(r"[ \t\f\v]+", " ", line).strip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_text(html: str) -> str:
    p = _HTMLText()
    p.feed(html)
    p.close()
    return tidy("".join(p.parts))


def _decode(part: _EmailMessage) -> str:
    charset = (part.get_content_charset() or "").lower()
    payload = part.get_payload(decode=True) or b""
    # Mail labelled gb2312 routinely carries GBK-only characters; gb18030 is the superset.
    for cs in (("gb18030",) if charset in _GB else (charset,)) + ("utf-8", "gb18030"):
        if not cs:
            continue
        try:
            return payload.decode(cs)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("latin-1", "replace")


def body_text(msg: _EmailMessage) -> str:
    plain, html, cal = [], [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        filename = (part.get_filename() or "").lower()
        if ctype in ("text/calendar", "application/ics") or filename.endswith(".ics"):
            cal.append(_decode(part))
        elif part.get_content_disposition() == "attachment":
            continue
        elif ctype == "text/plain":
            plain.append(tidy(_decode(part)))
        elif ctype == "text/html":
            html.append(html_to_text(_decode(part)))
    text_plain = "\n\n".join(t for t in plain if t)
    text_html = "\n\n".join(t for t in html if t)
    # Some senders ship a stub text/plain ("please view in an HTML client").
    text = text_plain if len(text_plain) >= min(200, len(text_html) // 5) else text_html
    text = text[:MAX_TEXT_CHARS]
    if cal:
        text += "\n\n[日历附件 text/calendar]\n" + "\n".join(c.strip() for c in cal)[:MAX_CALENDAR_CHARS]
    return text


def _header(msg: _EmailMessage, name: str) -> str:
    try:
        return str(msg.get(name, "") or "").strip()
    except Exception:  # malformed encoded-words: fall back to the raw bytes
        raw = msg.get_all(name, failobj=[""])[0]
        return str(raw).strip()


def parse_message(raw: bytes, mailbox: str, uid: int) -> Message:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    try:
        sent = parsedate_to_datetime(_header(msg, "Date"))
        if sent.tzinfo is None:  # "-0000": offset unknown
            sent = sent.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        sent = None
    return Message(
        mailbox=mailbox, uid=uid,
        message_id=_header(msg, "Message-ID").strip("<> "),
        date=sent, sender=_header(msg, "From"), subject=_header(msg, "Subject"),
        text=body_text(msg),
    )


def _quote(mailbox: str) -> str:
    return mailbox if mailbox.startswith('"') else '"' + mailbox.replace('"', '\\"') + '"'


class IMAPMailbox:
    def __init__(self, host: str, port: int, username: str, password: str, timeout: float = 60,
                 oauth_token: str | None = None):
        self.host, self.port, self.username, self._password, self.timeout = host, port, username, password, timeout
        self._token = oauth_token  # set for OAuth mailboxes (Outlook): SASL XOAUTH2 instead of LOGIN
        self.imap: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> IMAPMailbox:
        try:
            self.imap = imaplib.IMAP4_SSL(self.host, self.port, timeout=self.timeout)
        except OSError as e:
            raise MailError(f"连不上邮件服务器 {self.host}:{self.port}（{e}）") from None
        try:
            if self._token:
                raw = f"user={self.username}\x01auth=Bearer {self._token}\x01\x01".encode()
                self.imap.authenticate("XOAUTH2", lambda _: raw)
            else:
                self.imap.login(self.username, self._password)
        except imaplib.IMAP4.error as e:
            self.imap.shutdown()
            what = "登录令牌被拒（邮箱没开 IMAP，或令牌不含 IMAP 权限）" if self._token else "账号或 App 专用密码不对"
            raise MailError(f"邮箱登录失败：{what}（{e}）") from None
        if "ID" in self.imap.capabilities:
            # NetEase (163/126) refuses SELECT from clients that never identify
            # themselves ("Unsafe Login"); RFC 2971 ID is harmless elsewhere.
            imaplib.Commands.setdefault("ID", ("AUTH", "SELECTED"))
            try:
                self.imap._simple_command("ID", '("name" "Mailminder" "version" "0.1")')
            except imaplib.IMAP4.error:
                pass
        return self

    def __exit__(self, *exc):
        try:
            self.imap.logout()
        except Exception:
            pass

    def select(self, mailbox: str) -> tuple[int, int]:
        """EXAMINE (read-only); returns (UIDVALIDITY, UIDNEXT)."""
        typ, data = self.imap.select(_quote(mailbox), readonly=True)
        if typ != "OK":
            raise MailError(f"打不开邮件文件夹 {mailbox}: {data}")
        _, uv = self.imap.response("UIDVALIDITY")
        _, nxt = self.imap.response("UIDNEXT")
        return (int(uv[-1]) if uv and uv[-1] else 0), (int(nxt[-1]) if nxt and nxt[-1] else 0)

    def _search(self, *criteria: str) -> list[int]:
        typ, data = self.imap.uid("SEARCH", None, *criteria)
        if typ != "OK":
            raise MailError(f"搜索邮件失败: {data}")
        return sorted(int(x) for x in (data[0] or b"").split())

    def uids_since(self, day: date) -> list[int]:
        return self._search("SINCE", f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}")

    def uids_after(self, last_uid: int) -> list[int]:
        # "n:*" still returns the highest UID when nothing is newer than n.
        return [u for u in self._search("UID", f"{last_uid + 1}:*") if u > last_uid]

    def flags(self, uids: list[int]) -> dict[int, str]:
        typ, data = self.imap.uid("FETCH", ",".join(map(str, uids)), "(UID FLAGS)")
        out = {}
        for line in data or []:
            if isinstance(line, bytes):
                m = re.search(rb"UID (\d+).*FLAGS \(([^)]*)\)", line)
                if m:
                    out[int(m.group(1))] = m.group(2).decode()
        return out

    def fetch(self, mailbox: str, uids: list[int]) -> list[Message]:
        out = []
        for i in range(0, len(uids), 20):
            chunk = uids[i:i + 20]
            typ, data = self.imap.uid("FETCH", ",".join(map(str, chunk)),
                                      f"(UID BODY.PEEK[]<0.{MAX_FETCH_BYTES}>)")
            if typ != "OK":
                raise MailError(f"读取邮件失败: {data}")
            for item in data:
                if isinstance(item, tuple) and (m := re.search(rb"UID (\d+)", item[0])):
                    out.append(parse_message(item[1], mailbox, int(m.group(1))))
        return sorted(out, key=lambda msg: msg.uid)
