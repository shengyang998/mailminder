"""Read-only fetch from the real mailbox must not change any flag."""

import pytest

from live import imap_account
from mailminder.mailbox import IMAPMailbox

pytestmark = pytest.mark.live


def test_peek_fetch_leaves_flags_untouched():
    host, port, user, password = imap_account()
    with IMAPMailbox(host, port, user, password) as mb:
        uidvalidity, uidnext = mb.select("INBOX")
        assert uidvalidity > 0 and uidnext > 1
        uids = mb.uids_after(0)[-5:]
        assert uids, "INBOX is empty"
        before = mb.flags(uids)
        messages = mb.fetch("INBOX", uids)
        after = mb.flags(uids)
    assert before == after
    assert [m.uid for m in messages] == uids
    assert all(m.key for m in messages)
    assert sum(bool(m.text) for m in messages) >= len(messages) - 1
