"""Microsoft sign-in for Outlook.com / Hotmail / Microsoft 365 mailboxes.

Microsoft turned off password (Basic) authentication for IMAP — Outlook.com on
2024-09-16, Microsoft 365 earlier — so these mailboxes need OAuth. We use the
device code flow: the user opens microsoft.com/devicelogin on any device (a
phone works), types a short code and signs in; we receive tokens over a back
channel. The long-lived refresh token is kept in the Keychain in place of a
password and exchanged for a one-hour access token at the start of every run;
Microsoft rotates it, so the new one is stored back each time.

A registered app (client ID) is required: Microsoft only issues these tokens to
apps registered in Microsoft Entra. See README「Outlook」for the five-minute setup.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

AUTHORITY = "https://login.microsoftonline.com"
IMAP_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"
SIGN_IN_SCOPE = IMAP_SCOPE + " openid email"  # the id_token tells us which address signed in
IMAP_HOST = "outlook.office365.com"
# Shown instead of the endpoint's verification_uri (login.microsoft.com/device), which in
# iPhone Safari stalled on "verifying" and offered to download "nativeclient.".
DEVICE_PAGE = "https://microsoft.com/devicelogin"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
# The "Mailminder" app registration (any Entra directory + personal Microsoft
# accounts, public client flows on). A client ID is public by design; an account
# can point at its own registration instead via `client_id` in config.toml.
DEFAULT_CLIENT_ID = "1bcbe0b6-f6d3-48b9-a29e-8fa15190de63"


class OAuthError(RuntimeError):
    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


def _post(url: str, fields: dict[str, str], timeout: float = 30) -> tuple[int, dict]:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:  # 4xx carry a JSON error body we need to read
        try:
            return e.code, json.loads(e.read() or b"{}")
        except json.JSONDecodeError:
            return e.code, {"error": f"http_{e.code}"}


def _error(body: dict) -> OAuthError:
    code = str(body.get("error") or "unknown")
    desc = str(body.get("error_description") or "").split("\r\n")[0]
    hint = {
        "invalid_grant": "登录已失效（过期或被撤销），运行 mailminder account password 重新登录",
        "authorization_declined": "登录被拒绝",
        "expired_token": "验证码过期了，重新来一次",
        "invalid_client": "应用 ID 不对，或应用没打开「允许公共客户端流」",
        "unauthorized_client": "这个应用不允许这种登录方式（检查支持的账户类型和「允许公共客户端流」）",
    }.get(code, "")
    # The AADSTS number is more specific than the OAuth error code.
    for aadsts, text in (
        ("AADSTS700038", "应用 ID 不对：找不到这个应用"),
        ("AADSTS700016", "应用 ID 不对：找不到这个应用"),
        ("AADSTS7000218", "应用没打开「允许公共客户端流」：在应用的「身份验证」里设为「是」"),
        ("AADSTS9002331", "这个应用只支持个人账户：把 tenant 设成 consumers"),
        ("AADSTS50194", "这个应用是单租户的：把 tenant 设成你的目录（租户）ID"),
        ("AADSTS65001", "你的组织要求管理员先同意这个应用"),
        ("AADSTS90094", "你的组织要求管理员先同意这个应用"),
    ):
        if aadsts in desc:
            hint = text
            break
    return OAuthError(f"微软登录失败：{hint or code}（{desc[:160]}）", code)


def start_device_login(client_id: str, tenant: str = "common", post=_post) -> DeviceCode:
    status, body = post(f"{AUTHORITY}/{tenant}/oauth2/v2.0/devicecode",
                        {"client_id": client_id, "scope": SIGN_IN_SCOPE})
    if status != 200 or "device_code" not in body:
        raise _error(body)
    return DeviceCode(body["device_code"], body["user_code"], body.get("verification_uri", "https://microsoft.com/devicelogin"),
                      int(body.get("expires_in", 900)), int(body.get("interval", 5)))


def finish_device_login(client_id: str, tenant: str, code: DeviceCode, post=_post,
                        sleep=time.sleep, clock=time.monotonic) -> dict:
    """Poll until the user finishes signing in; returns the token response."""
    interval, deadline = code.interval, clock() + code.expires_in
    while clock() < deadline:
        sleep(interval)
        status, body = post(f"{AUTHORITY}/{tenant}/oauth2/v2.0/token",
                            {"grant_type": DEVICE_GRANT, "client_id": client_id, "device_code": code.device_code})
        if status == 200 and "access_token" in body:
            if not body.get("refresh_token"):
                raise OAuthError("微软没有给 refresh token（缺 offline_access 授权），后台任务没法长期运行")
            return body
        error = body.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise _error(body)
    raise OAuthError("等太久了，验证码已过期，重新来一次", "expired_token")


def refresh(client_id: str, tenant: str, refresh_token: str, post=_post) -> dict:
    status, body = post(f"{AUTHORITY}/{tenant}/oauth2/v2.0/token",
                        {"grant_type": "refresh_token", "client_id": client_id,
                         "refresh_token": refresh_token, "scope": IMAP_SCOPE})
    if status != 200 or "access_token" not in body:
        raise _error(body)
    return body


def signed_in_address(tokens: dict) -> str | None:
    """The address from the sign-in's id_token (received straight from the token endpoint over TLS)."""
    try:
        payload = tokens["id_token"].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (KeyError, IndexError, ValueError):
        return None
    address = claims.get("email") or claims.get("preferred_username")
    return address if isinstance(address, str) and "@" in address else None
