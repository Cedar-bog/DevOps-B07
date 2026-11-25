#!/usr/bin/env python3
"""Manage Basic Auth users for the B07 HTTP endpoints.

Commands:
    newuser <username>
    userlist
    deleteuser <username>

The CLI runs from the repository checkout and uses scripts/remote/remote.sh to
update /etc/nginx/.htpasswd-b07 on the server. Passwords are generated locally,
sent over SSH stdin, and never written to a command line or a local file.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shlex
import string
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REMOTE = REPO_ROOT / "scripts" / "remote" / "remote.sh"
HTPASSWD = "/etc/nginx/.htpasswd-b07"
LOCKFILE = HTPASSWD + ".lock"
USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
PASSWORD_ALPHABET = string.ascii_letters + string.digits
PASSWORD_LENGTH = 24


def _remote(script: str, *, stdin: str | None = None) -> subprocess.CompletedProcess:
    if not REMOTE.is_file() or not os.access(REMOTE, os.X_OK):
        raise RuntimeError(f"remote wrapper is missing or not executable: {REMOTE}")
    return subprocess.run(
        [str(REMOTE), "--shell", script],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def _username(value: str) -> str:
    if not USER_RE.fullmatch(value):
        raise ValueError(
            "username must start with a letter or digit and contain only "
            "letters, digits, dot, underscore or hyphen, up to 32 characters"
        )
    return value


def _common_script(username: str) -> str:
    return f"""set -eu
file={shlex.quote(HTPASSWD)}
lock={shlex.quote(LOCKFILE)}
username={shlex.quote(username)}
umask 077
exec 9>"$lock"
flock -x 9
"""


def newuser(username: str) -> int:
    try:
        username = _username(username)
    except ValueError as exc:
        print(f"newuser: {exc}", file=sys.stderr)
        return 2

    password = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))
    script = _common_script(username) + f"""if [ -f "$file" ] && cut -d: -f1 "$file" | grep -Fxq "$username"; then
    echo "user already exists" >&2
    exit 3
fi
if [ ! -f "$file" ]; then
    install -m 640 -o root -g www-data /dev/null "$file"
fi
htpasswd -B -i "$file" "$username" >/dev/null
chown root:www-data "$file"
chmod 640 "$file"
"""
    try:
        result = _remote(script, stdin=password + "\n")
    except RuntimeError as exc:
        print(f"newuser: {exc}", file=sys.stderr)
        return 1

    if result.returncode == 3:
        print(f"newuser: user already exists: {username}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        detail = result.stderr.strip() or f"remote exit code {result.returncode}"
        print(f"newuser: {detail}", file=sys.stderr)
        return 1

    print(f"username={username}")
    print(f"password={password}")
    return 0


def userlist() -> int:
    script = f"""set -eu
file={shlex.quote(HTPASSWD)}
if [ -f "$file" ]; then
    cut -d: -f1 "$file" | sort
fi
"""
    try:
        result = _remote(script)
    except RuntimeError as exc:
        print(f"userlist: {exc}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        detail = result.stderr.strip() or f"remote exit code {result.returncode}"
        print(f"userlist: {detail}", file=sys.stderr)
        return 1
    print(result.stdout, end="")
    return 0


def deleteuser(username: str) -> int:
    try:
        username = _username(username)
    except ValueError as exc:
        print(f"deleteuser: {exc}", file=sys.stderr)
        return 2

    script = _common_script(username) + f"""if [ ! -f "$file" ] || ! cut -d: -f1 "$file" | grep -Fxq "$username"; then
    echo "user not found" >&2
    exit 4
fi
htpasswd -D "$file" "$username" >/dev/null
chown root:www-data "$file"
chmod 640 "$file"
"""
    try:
        result = _remote(script)
    except RuntimeError as exc:
        print(f"deleteuser: {exc}", file=sys.stderr)
        return 1

    if result.returncode == 4:
        print(f"deleteuser: user not found: {username}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        detail = result.stderr.strip() or f"remote exit code {result.returncode}"
        print(f"deleteuser: {detail}", file=sys.stderr)
        return 1

    print(f"deleted={username}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="b07-users",
        description="Manage Basic Auth users for the B07 HTTP endpoints.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("newuser", help="create a user and print its password")
    create.add_argument("username")

    sub.add_parser("userlist", help="list usernames")

    delete = sub.add_parser("deleteuser", help="delete a user")
    delete.add_argument("username")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "newuser":
        return newuser(args.username)
    if args.command == "userlist":
        return userlist()
    if args.command == "deleteuser":
        return deleteuser(args.username)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
