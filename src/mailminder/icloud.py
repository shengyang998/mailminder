"""The iCloud account this Mac is signed into, read from MobileMeAccounts.plist.

Gives the Apple ID, the iCloud Mail address and IMAP host, and the CalDAV host
(including the account's partition and the China-mainland .cn service), so the
user only has to paste an app-specific password.
"""

from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path

PLIST = Path.home() / "Library" / "Preferences" / "MobileMeAccounts.plist"
FALLBACK_IMAP = "imap.mail.me.com"
FALLBACK_CALDAV = ("https://caldav.icloud.com", "https://caldav.icloud.com.cn")
APP_PASSWORD_HELP = ("打开 https://account.apple.com 登录，在「登录和安全」里选「App 专用密码」→「生成 App 专用密码」。"
                     "（需要 Apple 账户已开启双重认证）")


@dataclass
class ICloudAccount:
    apple_id: str
    mail_address: str | None
    imap_host: str | None
    caldav_url: str | None

    def imap_usernames(self) -> list[str]:
        """Apple's IMAP login is usually the bare local part; try the fuller forms after it."""
        names = []
        if self.mail_address:
            names += [self.mail_address.split("@")[0], self.mail_address]
        names.append(self.apple_id)
        return list(dict.fromkeys(n for n in names if n))


def detect(path: Path = PLIST) -> ICloudAccount | None:
    try:
        with path.open("rb") as f:
            data = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException):
        return None
    for acct in data.get("Accounts", []):
        apple_id = acct.get("AccountID")
        if not apple_id:
            continue
        services = {s.get("Name"): s for s in acct.get("Services", []) if isinstance(s, dict)}
        mail = services.get("MAIL_AND_NOTES") or {}
        cal = services.get("CALENDAR") or {}
        return ICloudAccount(
            apple_id=apple_id,
            mail_address=mail.get("EmailAddress") if mail.get("Enabled", True) else None,
            imap_host=mail.get("imapHostname"),
            caldav_url=cal.get("url"),
        )
    return None
