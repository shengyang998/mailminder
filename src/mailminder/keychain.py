"""Secrets live only in the login Keychain, via /usr/bin/security.

Items are created and read by the same binary, so the scheduled run under
launchd reads them without an access prompt. Secrets never go through argv.
"""

from __future__ import annotations

import subprocess

SECURITY = "/usr/bin/security"
MAIL_SERVICE = "mailminder.mail"
CALENDAR_SERVICE = "mailminder.calendar"
PROMPT_LIMIT = 128  # security's password prompt silently truncates longer input


class KeychainError(RuntimeError):
    pass


def get(service: str, account: str) -> str | None:
    r = subprocess.run([SECURITY, "find-generic-password", "-s", service, "-a", account, "-w"],
                       capture_output=True, text=True)
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


def put(service: str, account: str, secret: str, label: str | None = None) -> None:
    if not secret or "\n" in secret:
        raise KeychainError("密码为空或含换行")
    fields = [service, account, label or ""]
    if not any('"' in f or "\\" in f for f in fields + [secret]):
        # Interactive mode reads the whole command from stdin, so the secret stays
        # out of argv and there is no length limit (OAuth refresh tokens run ~2 KB).
        cmd = f'add-generic-password -U -s "{service}" -a "{account}"'
        cmd += f' -l "{label}"' if label else ""
        r = subprocess.run([SECURITY, "-i"], input=f'{cmd} -w "{secret}"\n', capture_output=True, text=True)
    elif len(secret) <= PROMPT_LIMIT and not any('"' in f for f in fields):
        # A bare trailing -w prompts for the secret (entry + retype), answered on stdin.
        args = [SECURITY, "add-generic-password", "-U", "-s", service, "-a", account]
        args += ["-l", label] if label else []
        r = subprocess.run(args + ["-w"], input=f"{secret}\n{secret}\n", capture_output=True, text=True)
    else:
        raise KeychainError("密码含引号且过长，存不进钥匙串")
    if get(service, account) != secret:
        raise KeychainError(f"写入钥匙串失败: {r.stderr.strip() or '读回不一致'}")


def delete(service: str, account: str) -> bool:
    r = subprocess.run([SECURITY, "delete-generic-password", "-s", service, "-a", account],
                       capture_output=True, text=True)
    return r.returncode == 0
