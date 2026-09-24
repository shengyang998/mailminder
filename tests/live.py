"""Credentials for live tests: the installed config if present, else MM_* env vars."""

import os

from mailminder import keychain


def config_account(account: dict) -> str:
    from mailminder.config import mail_secret_account
    return mail_secret_account(account)


def _config():
    try:
        from mailminder import config
        return config.load()
    except Exception:
        return None


def caldav_account() -> tuple[str, str, str]:
    cfg = _config()
    if cfg:
        user = cfg["calendar"]["username"]
        return cfg["calendar"]["url"], user, keychain.get(keychain.CALENDAR_SERVICE, user)
    user = os.environ["MM_CALDAV_USER"]
    service = os.environ.get("MM_CALDAV_KEYCHAIN_SERVICE", keychain.CALENDAR_SERVICE)
    return os.environ["MM_CALDAV_URL"], user, keychain.get(service, user)


def imap_account() -> tuple[str, int, str, str]:
    cfg = _config()
    if cfg and cfg["accounts"]:
        m = cfg["accounts"][0]
        return (m["host"], int(m.get("port", 993)), m["username"],
                keychain.get(keychain.MAIL_SERVICE, config_account(m)))
    user = os.environ["MM_IMAP_USER"]
    service = os.environ.get("MM_IMAP_KEYCHAIN_SERVICE", keychain.MAIL_SERVICE)
    account = os.environ.get("MM_IMAP_KEYCHAIN_ACCOUNT", user)
    return os.environ["MM_IMAP_HOST"], 993, user, keychain.get(service, account)
