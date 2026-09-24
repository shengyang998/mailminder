"""Secrets live only in the login Keychain, via /usr/bin/security.

Items are created and read by the same binary, so the scheduled run under
launchd reads them without an access prompt.
"""

from __future__ import annotations

import subprocess

SECURITY = "/usr/bin/security"
MAIL_SERVICE = "mailminder.mail"
CALENDAR_SERVICE = "mailminder.calendar"


class KeychainError(RuntimeError):
    pass


def get(service: str, account: str) -> str | None:
    r = subprocess.run([SECURITY, "find-generic-password", "-s", service, "-a", account, "-w"],
                       capture_output=True, text=True)
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


def put(service: str, account: str, secret: str, label: str | None = None) -> None:
    if not secret or "\n" in secret:
        raise KeychainError("密码为空或含换行")
    args = [SECURITY, "add-generic-password", "-U", "-s", service, "-a", account]
    if label:
        args += ["-l", label]
    # A trailing bare -w makes security prompt for the secret, and it reads the
    # prompt answers (entry + retype) from stdin, so the secret never shows in argv.
    args.append("-w")
    r = subprocess.run(args, input=f"{secret}\n{secret}\n", capture_output=True, text=True)
    if r.returncode != 0:
        raise KeychainError(f"写入钥匙串失败: {r.stderr.strip()}")
    if get(service, account) != secret:
        raise KeychainError("写入钥匙串后读回不一致")


def delete(service: str, account: str) -> bool:
    r = subprocess.run([SECURITY, "delete-generic-password", "-s", service, "-a", account],
                       capture_output=True, text=True)
    return r.returncode == 0
