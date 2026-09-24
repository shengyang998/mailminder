"""Real login Keychain: long OAuth tokens and odd characters survive the round trip."""

import secrets
import string

import pytest

from mailminder import keychain

pytestmark = pytest.mark.live
SERVICE = "mailminder.test"


@pytest.mark.parametrize("secret", [
    "M.C5" + "".join(secrets.choice(string.ascii_letters + string.digits + "!*$.-_") for _ in range(1800)),
    "-".join("".join(secrets.choice(string.ascii_lowercase) for _ in range(4)) for _ in range(4)),  # app-password shape
    'pa"ss\\word',  # quotes/backslashes take the prompt path (short secrets only)
])
def test_round_trip(secret):
    try:
        keychain.put(SERVICE, "probe", secret, label="Mailminder 测试")
        assert keychain.get(SERVICE, "probe") == secret
    finally:
        keychain.delete(SERVICE, "probe")
