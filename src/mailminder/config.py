"""Settings in ~/.config/mailminder/config.toml; run state in ~/.local/state/mailminder/.

MAILMINDER_HOME relocates both (used by tests).
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .timez import system_zone


def _home() -> Path | None:
    return Path(os.environ["MAILMINDER_HOME"]) if os.environ.get("MAILMINDER_HOME") else None


def config_path() -> Path:
    base = _home() or Path.home() / ".config" / "mailminder"
    return base / "config.toml"


def state_dir() -> Path:
    base = _home() or Path.home() / ".local" / "state" / "mailminder"
    base.mkdir(parents=True, exist_ok=True)
    return base


def work_dir() -> Path:
    """Empty working directory for the headless agent (no project files to pick up)."""
    d = state_dir() / "work"
    d.mkdir(exist_ok=True)
    return d


DEFAULT_MODELS = {"claude": "opus", "codex": ""}
EFFORTS = ("low", "medium", "high")


def defaults() -> dict:
    return {
        "timezone": system_zone(),
        "language": "zh-Hans",
        "interval_minutes": 30,
        "lookback_days": 7,
        "max_per_run": 50,
        "batch_size": 5,
        "ai": {"harness": "claude", "path": "", "model": "opus", "effort": "medium", "timeout": 600},
        "calendar": {"url": "", "username": "", "calendar_url": "", "calendar_name": "邮件提醒"},
        "accounts": [],
    }


class ConfigError(RuntimeError):
    pass


def load() -> dict:
    path = config_path()
    if not path.exists():
        raise ConfigError("还没有设置，先运行 mailminder init")
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    merged = defaults()
    for key, value in cfg.items():
        merged[key] = {**merged[key], **value} if isinstance(merged.get(key), dict) else value
    return merged


def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # a JSON string is a valid TOML basic string
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    raise TypeError(f"unsupported config value: {v!r}")


def dumps(cfg: dict) -> str:
    lines = [f"{k} = {_value(v)}" for k, v in cfg.items()
             if v is not None and not isinstance(v, dict) and not (isinstance(v, list) and v and isinstance(v[0], dict))]
    for k, v in cfg.items():
        if isinstance(v, dict):
            lines += ["", f"[{k}]"] + [f"{kk} = {_value(vv)}" for kk, vv in v.items() if vv is not None]
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            for item in v:
                lines += ["", f"[[{k}]]"] + [f"{kk} = {_value(vv)}" for kk, vv in item.items() if vv is not None]
    return "\n".join(lines) + "\n"


def save(cfg: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dumps(cfg), encoding="utf-8")
    tmp.replace(path)


def mail_secret_account(account: dict) -> str:
    return f"{account['username']}@{account['host']}"


# `mailminder config set <key> <value>`: key → (section or None, field, parser)
def _zone(v: str) -> str:
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"不认识的时区：{v}（用 IANA 名，如 Asia/Shanghai）") from None
    return v


def _language(v: str) -> str:
    if v not in ("zh-Hans", "en"):
        raise ConfigError("language 只能是 zh-Hans 或 en")
    return v


def _positive(v: str) -> int:
    n = int(v)
    if n <= 0:
        raise ConfigError("要正整数")
    return n


def _effort(v: str) -> str:
    if v not in EFFORTS:
        raise ConfigError(f"effort 只能是 {'/'.join(EFFORTS)}")
    return v


SETTABLE = {
    "timezone": (None, "timezone", _zone),
    "language": (None, "language", _language),
    "interval": (None, "interval_minutes", _positive),
    "lookback": (None, "lookback_days", _positive),
    "max-per-run": (None, "max_per_run", _positive),
    "batch-size": (None, "batch_size", _positive),
    "effort": ("ai", "effort", _effort),
    "timeout": ("ai", "timeout", _positive),
}


def set_value(cfg: dict, key: str, raw: str) -> None:
    if key not in SETTABLE:
        raise ConfigError(f"不能直接设置 {key}；可设置：{', '.join(SETTABLE)}（模型用 mailminder model）")
    section, field, parse = SETTABLE[key]
    try:
        value = parse(raw)
    except ValueError:
        raise ConfigError(f"{key} 的值不对：{raw}") from None
    (cfg[section] if section else cfg)[field] = value
