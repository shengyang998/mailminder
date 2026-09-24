"""Accuracy eval: real model through extract() + plan() on synthetic emails.

MM_EVAL_CLAUDE_MODEL / MM_EVAL_CODEX_MODEL pick the models (default opus / gpt-6-astra).
Run with -s to see the per-email table.
"""

import os
import shutil
import tempfile
from datetime import datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import pytest

from eval_cases import CASES
from mailminder.extract import extract
from mailminder.mailbox import Message
from mailminder.plan import plan

pytestmark = pytest.mark.live
SH = "Asia/Shanghai"
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=ZoneInfo(SH))
HARNESSES = {
    "claude": (shutil.which("claude") or os.path.expanduser("~/.local/bin/claude"),
               os.environ.get("MM_EVAL_CLAUDE_MODEL", "opus")),
    "codex": (shutil.which("codex") or "/usr/local/bin/codex", os.environ.get("MM_EVAL_CODEX_MODEL", "gpt-6-astra")),
}


def as_message(case) -> Message:
    return Message("INBOX", 0, f"{case['id']}@eval", parsedate_to_datetime(case["sent"]),
                   case["sender"], case["subject"], case["body"])


def shown(start) -> str:
    return start.strftime("%Y-%m-%dT%H:%MZ") if isinstance(start, datetime) else start.isoformat()


@pytest.mark.parametrize("harness", sorted(HARNESSES))
def test_extraction_accuracy(harness):
    path, model = HARNESSES[harness]
    if not os.path.exists(path):
        pytest.skip(f"{harness} not installed")
    ai = {"harness": harness, "path": path, "model": model, "effort": "medium", "timeout": 600}
    messages = [as_message(c) for c in CASES]
    raw = {}
    with tempfile.TemporaryDirectory() as work:
        for i in range(0, len(messages), 4):
            raw.update(extract(messages[i:i + 4], ai, SH, "zh-Hans", NOW, work))
    failures, rows = [], []
    for case, msg in zip(CASES, messages, strict=True):
        planned = [plan(r, msg, SH, NOW) for r in raw.get(msg.key, [])]
        got = {(p.status, p.event.all_day, shown(p.event.start.astimezone(ZoneInfo("UTC"))
                                                  if isinstance(p.event.start, datetime) else p.event.start))
               for p in planned if not isinstance(p, str)}
        want, optional = set(case["expect"]), set(case.get("optional", []))
        ok = msg.key in raw and want <= got and not (got - want - optional)
        titles = [p.event.title for p in planned if not isinstance(p, str)]
        rows.append(f"{'OK ' if ok else 'BAD'} {case['id']:28} got={sorted(got)} titles={titles}")
        if not ok:
            failures.append(case["id"])
    report = f"\n[{harness} / {model}]\n" + "\n".join(rows)
    print(report)
    assert not failures, report
