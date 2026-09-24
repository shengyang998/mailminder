import plistlib
import tomllib

import pytest

from mailminder import config
from mailminder.icloud import detect


def test_dump_and_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("MAILMINDER_HOME", str(tmp_path))
    cfg = config.defaults()
    cfg["calendar"].update(url="https://caldav.example", username='a"b', calendar_name="邮件提醒")
    cfg["accounts"] = [{"name": "iCloud", "host": "imap.example", "port": 993, "username": "me",
                        "mailboxes": ["INBOX", "Archive"]}]
    config.save(cfg)
    assert tomllib.loads(config.config_path().read_text()) == {k: v for k, v in cfg.items()}
    assert config.load() == cfg


def test_load_fills_in_new_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("MAILMINDER_HOME", str(tmp_path))
    config.config_path().parent.mkdir(parents=True, exist_ok=True)
    config.config_path().write_text('timezone = "Asia/Tokyo"\n[ai]\nharness = "codex"\n')
    cfg = config.load()
    assert cfg["timezone"] == "Asia/Tokyo" and cfg["ai"]["harness"] == "codex"
    assert cfg["ai"]["timeout"] == 600 and cfg["interval_minutes"] == 30


def test_missing_config_says_how_to_start(tmp_path, monkeypatch):
    monkeypatch.setenv("MAILMINDER_HOME", str(tmp_path))
    with pytest.raises(config.ConfigError, match="mailminder init"):
        config.load()


def test_set_value_validates():
    cfg = config.defaults()
    config.set_value(cfg, "timezone", "Europe/London")
    config.set_value(cfg, "interval", "15")
    config.set_value(cfg, "effort", "low")
    assert (cfg["timezone"], cfg["interval_minutes"], cfg["ai"]["effort"]) == ("Europe/London", 15, "low")
    for key, bad in [("timezone", "Mars/Base"), ("timezone", "+08:00"), ("interval", "0"), ("interval", "x"),
                     ("effort", "max"), ("model", "opus")]:
        with pytest.raises(config.ConfigError):
            config.set_value(cfg, key, bad)


def test_icloud_detection_reads_the_signed_in_account(tmp_path):
    path = tmp_path / "MobileMeAccounts.plist"
    with path.open("wb") as f:
        plistlib.dump({"Accounts": [{"AccountID": "someone@example.com", "Services": [
            {"Name": "MAIL_AND_NOTES", "Enabled": True, "EmailAddress": "someone@icloud.com",
             "imapHostname": "p99-imap.mail.icloud.com.cn"},
            {"Name": "CALENDAR", "Enabled": True, "url": "https://p99-caldav.icloud.com.cn:443"},
        ]}]}, f)
    acct = detect(path)
    assert acct.apple_id == "someone@example.com"
    assert acct.imap_host == "p99-imap.mail.icloud.com.cn"
    assert acct.caldav_url == "https://p99-caldav.icloud.com.cn:443"
    assert acct.imap_usernames() == ["someone", "someone@icloud.com", "someone@example.com"]
    assert detect(tmp_path / "missing.plist") is None
