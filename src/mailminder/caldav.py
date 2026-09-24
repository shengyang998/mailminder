"""Minimal CalDAV client (RFC 4791): discovery, calendars, event PUT/GET/DELETE.

Works against iCloud (global and the China-mainland icloud.com.cn service) and
any standards-following server. Auth is HTTP Basic with an app-specific password.
"""

from __future__ import annotations

import base64
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urljoin
from xml.sax.saxutils import escape

NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav", "a": "http://apple.com/ns/ical/"}
_D, _C = "{DAV:}", "{urn:ietf:params:xml:ns:caldav}"


class CalDAVError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Calendar:
    url: str
    name: str
    components: list[str]
    writable: bool


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # urllib only re-issues GET/HEAD on a redirect; follow them ourselves so
    # PROPFIND/PUT keep their method and body.
    def redirect_request(self, *args, **kwargs):
        return None


class CalDAV:
    def __init__(self, url: str, username: str, password: str, timeout: float = 30):
        self.url = url if url.endswith("/") else url + "/"
        self._auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        self._timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    def request(self, method: str, url: str, body: str | bytes | None = None,
                headers: dict[str, str] | None = None, depth: int | None = None):
        hdrs = {"Authorization": self._auth, "User-Agent": "Mailminder"}
        if isinstance(body, str):
            body = body.encode()
        if body is not None:
            hdrs["Content-Type"] = "application/xml; charset=utf-8"
        if depth is not None:
            hdrs["Depth"] = str(depth)
        hdrs.update(headers or {})
        for _ in range(4):
            req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
            try:
                with self._opener.open(req, timeout=self._timeout) as r:
                    return r.status, r.headers, r.read()
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 307, 308) and e.headers.get("Location"):
                    url = urljoin(url, e.headers["Location"])
                    continue
                if e.code == 401:
                    raise CalDAVError("日历登录失败：账号或 App 专用密码不对", 401) from None
                return e.code, e.headers, e.read()
        raise CalDAVError(f"重定向过多: {url}")

    def _propfind(self, url: str, props: str, depth: int) -> list[tuple[str, dict[str, ET.Element]]]:
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" '
                f'xmlns:a="http://apple.com/ns/ical/"><d:prop>{props}</d:prop></d:propfind>')
        status, _, data = self.request("PROPFIND", url, body, depth=depth)
        if status != 207:
            raise CalDAVError(f"PROPFIND {url} → HTTP {status}", status)
        out = []
        for resp in ET.fromstring(data).findall("d:response", NS):
            href = urljoin(url, (resp.findtext("d:href", default="", namespaces=NS)).strip())
            found: dict[str, ET.Element] = {}
            for ps in resp.findall("d:propstat", NS):
                prop = ps.find("d:prop", NS)
                if " 200 " in f" {ps.findtext('d:status', default='', namespaces=NS)} " and prop is not None:
                    found.update({child.tag: child for child in prop})
            out.append((href, found))
        return out

    def discover_home(self) -> str:
        """Principal → calendar-home-set. The home may live on another host (iCloud partitions)."""
        principal = None
        for _, props in self._propfind(self.url, "<d:current-user-principal/>", 0):
            el = props.get(_D + "current-user-principal")
            href = el.findtext("d:href", namespaces=NS) if el is not None else None
            if href:
                principal = urljoin(self.url, href.strip())
        if not principal:
            raise CalDAVError("服务器没有返回 CalDAV 账户地址")
        for _, props in self._propfind(principal, "<c:calendar-home-set/>", 0):
            el = props.get(_C + "calendar-home-set")
            href = el.findtext("d:href", namespaces=NS) if el is not None else None
            if href:
                return urljoin(principal, href.strip())
        raise CalDAVError("服务器没有返回日历主目录")

    def calendars(self, home: str) -> list[Calendar]:
        props = ("<d:displayname/><d:resourcetype/><c:supported-calendar-component-set/>"
                 "<d:current-user-privilege-set/>")
        cals = []
        for href, p in self._propfind(home, props, 1):
            rt = p.get(_D + "resourcetype")
            if rt is None or rt.find("c:calendar", NS) is None:
                continue
            comp_set = p.get(_C + "supported-calendar-component-set")
            comps = [c.get("name", "") for c in comp_set] if comp_set is not None else []
            privset = p.get(_D + "current-user-privilege-set")
            privs = {g.tag for pr in privset for g in pr} if privset is not None else set()
            writable = bool(privs & {_D + "write", _D + "write-content", _D + "bind", _D + "all"})
            name_el = p.get(_D + "displayname")
            name = (name_el.text or "").strip() if name_el is not None else ""
            cals.append(Calendar(href, name, comps, writable))
        return cals

    def make_calendar(self, home: str, name: str, color: str = "#2F8F83FF") -> Calendar:
        url = urljoin(home, f"{str(uuid.uuid4()).upper()}/")
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                '<c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" '
                'xmlns:a="http://apple.com/ns/ical/"><d:set><d:prop>'
                f"<d:displayname>{escape(name)}</d:displayname>"
                '<c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>'
                f"<a:calendar-color>{color}</a:calendar-color>"
                "</d:prop></d:set></c:mkcalendar>")
        status, _, data = self.request("MKCALENDAR", url, body)
        if status not in (200, 201):
            raise CalDAVError(f"新建日历失败: HTTP {status} {data[:200]!r}", status)
        return Calendar(url, name, ["VEVENT"], True)

    def put_event(self, url: str, ics: str, *, create_only: bool) -> tuple[int, str | None]:
        """create_only → If-None-Match: *; returns 412 (not an error) when the event already exists."""
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if create_only:
            headers["If-None-Match"] = "*"
        status, hdrs, data = self.request("PUT", url, ics, headers)
        if status in (200, 201, 204) or (status == 412 and create_only):
            return status, hdrs.get("ETag")
        raise CalDAVError(f"写入日程失败: HTTP {status} {data[:300]!r}", status)

    def get(self, url: str) -> tuple[str, str | None] | None:
        status, hdrs, data = self.request("GET", url)
        if status == 404:
            return None
        if status != 200:
            raise CalDAVError(f"读取日程失败: HTTP {status}", status)
        return data.decode("utf-8", "replace"), hdrs.get("ETag")

    def delete(self, url: str) -> bool:
        """True if deleted, False if it was already gone."""
        status, _, _ = self.request("DELETE", url)
        if status in (200, 204):
            return True
        if status == 404:
            return False
        raise CalDAVError(f"删除失败: HTTP {status}", status)
