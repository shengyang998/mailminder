"""The scheduled run as a per-user launchd agent, plus a one-shot self-test job.

The self-test runs `mailminder doctor` inside launchd — the same context as the
scheduled run (bare PATH, no terminal, its own Keychain access) — so anything
that would only fail at 3 a.m. fails during setup instead.
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config

LABEL = "local.mailminder"
SELFTEST_LABEL = "local.mailminder.selftest"


def _plist(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True)


def path_env(harness_path: str) -> str:
    """launchd starts jobs with a bare PATH; add the agent CLI's folder (and node, which npm-installed codex needs)."""
    dirs = []
    for p in (harness_path, os.path.realpath(harness_path) if harness_path else "", shutil.which("node") or ""):
        if p:
            dirs.append(os.path.dirname(p))
    dirs += [str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin",
             "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    return ":".join(dict.fromkeys(d for d in dirs if d))


def _job(label: str, args: list[str], cfg: dict, log: Path, **extra) -> dict:
    env = {"PATH": path_env(cfg["ai"]["path"]), "HOME": str(Path.home()), "PYTHONUNBUFFERED": "1"}
    if os.environ.get("MAILMINDER_HOME"):
        env["MAILMINDER_HOME"] = os.environ["MAILMINDER_HOME"]
    return {"Label": label, "ProgramArguments": [sys.executable, "-m", "mailminder", *args],
            "EnvironmentVariables": env, "StandardOutPath": str(log), "StandardErrorPath": str(log),
            "ProcessType": "Background", **extra}


def _load(label: str, job: dict) -> None:
    path = _plist(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    _launchctl("bootout", f"{_domain()}/{label}")
    with path.open("wb") as f:
        plistlib.dump(job, f)
    r = _launchctl("bootstrap", _domain(), str(path))
    if r.returncode != 0:
        raise RuntimeError(f"后台任务装不上：{r.stderr.strip() or r.stdout.strip()}")


def install(cfg: dict) -> None:
    logs = config.state_dir() / "logs"
    logs.mkdir(exist_ok=True)
    _load(LABEL, _job(LABEL, ["run", "--scheduled"], cfg, logs / "agent.log",
                      StartInterval=int(cfg["interval_minutes"]) * 60, RunAtLoad=True))


def uninstall() -> bool:
    _launchctl("bootout", f"{_domain()}/{LABEL}")
    path = _plist(LABEL)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def status() -> dict | None:
    """None when not loaded; else launchd's view (state, last exit code, runs)."""
    r = _launchctl("print", f"{_domain()}/{LABEL}")
    if r.returncode != 0:
        return None
    info = {}
    for line in r.stdout.splitlines():
        key, sep, value = line.strip().partition(" = ")
        if sep and key in ("state", "last exit code", "runs", "run interval"):
            info[key] = value
    return info


def selftest(cfg: dict, timeout: float = 900) -> list[dict]:
    """Run `doctor --json` once under launchd and return its checks."""
    out = config.state_dir() / "selftest.json"
    out.unlink(missing_ok=True)
    log = config.state_dir() / "logs" / "selftest.log"
    log.parent.mkdir(exist_ok=True)
    _load(SELFTEST_LABEL, _job(SELFTEST_LABEL, ["doctor", "--write-test", "--json", str(out)], cfg, log,
                               RunAtLoad=True))
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not out.exists():
            time.sleep(2)
        if not out.exists():
            tail = log.read_text(errors="replace")[-600:] if log.exists() else ""
            return [{"name": "后台自检", "ok": False, "detail": f"{int(timeout)} 秒内没有结果。{tail}"}]
        return json.loads(out.read_text())
    finally:
        _launchctl("bootout", f"{_domain()}/{SELFTEST_LABEL}")
        _plist(SELFTEST_LABEL).unlink(missing_ok=True)
