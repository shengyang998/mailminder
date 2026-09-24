"""Opening a configured mailbox: app password, or Microsoft sign-in (Outlook).

Both kinds keep one secret in the Keychain under the account's key: the app
password, or the Microsoft refresh token that stands in for it.
"""

from __future__ import annotations

from . import config, keychain, oauth
from .mailbox import IMAPMailbox

MICROSOFT = "microsoft"


def is_microsoft(account: dict) -> bool:
    return account.get("auth") == MICROSOFT


def open_mailbox(account: dict, factory=IMAPMailbox, get=keychain.get, put=keychain.put):
    """An unopened mailbox context for the account (use it in a `with`)."""
    key = config.mail_secret_account(account)
    secret = get(keychain.MAIL_SERVICE, key)
    if not secret:
        raise config.ConfigError(f"钥匙串里没有 {key} 的登录信息，运行 mailminder account password 补上")
    host, port, user = account["host"], int(account.get("port", 993)), account["username"]
    if not is_microsoft(account):
        return factory(host, port, user, secret)
    tokens = oauth.refresh(account["client_id"], account.get("tenant") or "common", secret)
    rotated = tokens.get("refresh_token")
    if rotated and rotated != secret:  # Microsoft rotates refresh tokens; keep the newest
        put(keychain.MAIL_SERVICE, key, rotated, label=f"Mailminder 邮箱 {user}")
    return factory(host, port, user, "", oauth_token=tokens["access_token"])
