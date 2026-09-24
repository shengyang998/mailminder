import base64
import imaplib

import pytest

from mailminder import accounts, oauth
from mailminder.mailbox import IMAPMailbox, MailError


class FakeTokenServer:
    """Scripted responses for the device-code and token endpoints."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, url, fields):
        self.requests.append((url, fields))
        return self.responses.pop(0)


def device_code(interval=5):
    return oauth.DeviceCode("dev-123", "ABCD-EFGH", "https://microsoft.com/devicelogin", 900, interval)


def test_device_login_start_requests_imap_and_offline_access():
    server = FakeTokenServer((200, {"device_code": "d", "user_code": "U", "verification_uri": "https://x",
                                    "expires_in": 900, "interval": 5}))
    code = oauth.start_device_login("client-1", "common", post=server)
    url, fields = server.requests[0]
    assert url.endswith("/common/oauth2/v2.0/devicecode")
    assert fields["scope"].split() == ["https://outlook.office.com/IMAP.AccessAsUser.All", "offline_access",
                                       "openid", "email"]
    assert (code.device_code, code.user_code) == ("d", "U")


def test_polling_waits_backs_off_and_returns_tokens():
    server = FakeTokenServer(
        (400, {"error": "authorization_pending"}),
        (400, {"error": "slow_down"}),
        (200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}),
    )
    slept = []
    tokens = oauth.finish_device_login("client-1", "common", device_code(), post=server,
                                       sleep=slept.append, clock=lambda: 0)
    assert tokens["refresh_token"] == "rt"
    assert slept == [5, 5, 10]  # slow_down adds 5 s to the interval
    assert server.requests[0][1]["grant_type"] == oauth.DEVICE_GRANT


def test_declined_sign_in_is_an_error():
    server = FakeTokenServer((400, {"error": "authorization_declined", "error_description": "AADSTS70000"}))
    with pytest.raises(oauth.OAuthError, match="登录被拒绝"):
        oauth.finish_device_login("c", "common", device_code(), post=server, sleep=lambda s: None, clock=lambda: 0)


def test_missing_refresh_token_is_refused():
    server = FakeTokenServer((200, {"access_token": "at"}))
    with pytest.raises(oauth.OAuthError, match="refresh token"):
        oauth.finish_device_login("c", "common", device_code(), post=server, sleep=lambda s: None, clock=lambda: 0)


def test_code_expiry_stops_polling():
    ticks = iter([0, 1000])
    with pytest.raises(oauth.OAuthError, match="过期"):
        oauth.finish_device_login("c", "common", device_code(), post=FakeTokenServer(),
                                  sleep=lambda s: None, clock=lambda: next(ticks))


def test_error_hints_name_the_fix():
    wrong_id = {"error": "unauthorized_client",
                "error_description": "AADSTS700038: 0000 is not a valid application identifier."}
    with pytest.raises(oauth.OAuthError, match="应用 ID 不对"):
        oauth.start_device_login("c", "common", post=FakeTokenServer((400, wrong_id)))
    no_public_flow = {"error": "invalid_client",
                      "error_description": "AADSTS7000218: The request body must contain 'client_assertion' or 'client_secret'."}
    with pytest.raises(oauth.OAuthError, match="允许公共客户端流"):
        oauth.start_device_login("c", "common", post=FakeTokenServer((400, no_public_flow)))
    personal_only = {"error": "invalid_request",
                     "error_description": "AADSTS9002331: Application is configured for use by Microsoft Account users only."}
    with pytest.raises(oauth.OAuthError, match="consumers"):
        oauth.start_device_login("c", "common", post=FakeTokenServer((400, personal_only)))
    expired = {"error": "invalid_grant", "error_description": "AADSTS70008: The refresh token has expired"}
    with pytest.raises(oauth.OAuthError, match="重新登录"):
        oauth.refresh("c", "common", "old", post=FakeTokenServer((400, expired)))


class FakeFactory:
    def __init__(self):
        self.calls = []

    def __call__(self, host, port, user, password, **kw):
        self.calls.append((host, port, user, password, kw))
        return "mailbox"


def test_password_account_opens_with_its_keychain_secret():
    factory = FakeFactory()
    acct = {"name": "iCloud", "host": "imap.example", "port": 993, "username": "me"}
    got = accounts.open_mailbox(acct, factory, get=lambda s, a: "app-pw", put=None)
    assert got == "mailbox" and factory.calls == [("imap.example", 993, "me", "app-pw", {})]


def test_microsoft_account_refreshes_and_keeps_the_rotated_token(monkeypatch):
    seen = {}

    def fake_refresh(client_id, tenant, token):
        seen["args"] = (client_id, tenant, token)
        return {"access_token": "fresh-at", "refresh_token": "rotated-rt"}

    monkeypatch.setattr(oauth, "refresh", fake_refresh)
    stored = {}
    factory = FakeFactory()
    acct = {"name": "Outlook", "host": oauth.IMAP_HOST, "username": "me@outlook.com", "auth": "microsoft",
            "client_id": "cid", "tenant": "consumers"}
    accounts.open_mailbox(acct, factory, get=lambda s, a: "old-rt",
                          put=lambda s, a, v, label=None: stored.update({a: v}))
    assert seen["args"] == ("cid", "consumers", "old-rt")
    assert stored == {"me@outlook.com@outlook.office365.com": "rotated-rt"}
    assert factory.calls[0][4] == {"oauth_token": "fresh-at"} and factory.calls[0][3] == ""


class FakeIMAP:
    capabilities = ("IMAP4REV1", "AUTH=XOAUTH2")
    reject = False

    def __init__(self, host, port, timeout=None):
        self.auth = None

    def authenticate(self, mechanism, callback):
        self.auth = (mechanism, callback(b""))
        if FakeIMAP.reject:
            raise imaplib.IMAP4.error("AUTHENTICATE failed")
        return "OK", [b""]

    def login(self, *a):
        raise AssertionError("OAuth mailboxes must not use LOGIN")

    def shutdown(self):
        pass

    def logout(self):
        pass


def test_imap_uses_xoauth2_with_the_access_token(monkeypatch):
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    FakeIMAP.reject = False
    with IMAPMailbox(oauth.IMAP_HOST, 993, "me@outlook.com", "", oauth_token="tok") as mb:
        mechanism, raw = mb.imap.auth
    assert mechanism == "XOAUTH2"
    assert raw == b"user=me@outlook.com\x01auth=Bearer tok\x01\x01"
    base64.b64encode(raw)  # what imaplib sends on the wire


def test_rejected_token_explains_the_likely_cause(monkeypatch):
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    FakeIMAP.reject = True
    with pytest.raises(MailError, match="令牌被拒"):
        with IMAPMailbox(oauth.IMAP_HOST, 993, "me@outlook.com", "", oauth_token="tok"):
            pass


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(__import__("json").dumps(claims).encode()).rstrip(b"=").decode()
    return f"e30.{body}.sig"


def test_signed_in_address_comes_from_the_id_token():
    assert oauth.signed_in_address({"id_token": _jwt({"email": "me@outlook.com"})}) == "me@outlook.com"
    assert oauth.signed_in_address({"id_token": _jwt({"preferred_username": "me@hotmail.com"})}) == "me@hotmail.com"
    assert oauth.signed_in_address({"id_token": _jwt({"name": "Me"})}) is None
    assert oauth.signed_in_address({}) is None and oauth.signed_in_address({"id_token": "garbage"}) is None


def test_refresh_asks_only_for_the_imap_resource():
    server = FakeTokenServer((200, {"access_token": "at", "refresh_token": "rt2"}))
    oauth.refresh("c", "common", "rt", post=server)
    assert "openid" not in server.requests[0][1]["scope"]
