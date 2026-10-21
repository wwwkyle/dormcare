#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DormCare · 账号与会话 / Accounts, passwords and sessions
=======================================================

The app used to have no identity at all: "student side" and "technician side"
were two URLs, and every visitor could open the technician console. This module
supplies the missing half — who you are — so the server can decide what each
person is allowed to see.

Design notes
------------
* **Standard library only.** `hashlib.pbkdf2_hmac` for passwords,
  `secrets.token_urlsafe` for session tokens. Nothing to install.
* **Passwords are never stored, only a PBKDF2-SHA256 digest** with a per-user
  random salt and 200 000 rounds. Comparison is constant-time
  (`hmac.compare_digest`) so the endpoint cannot be used as a timing oracle.
* **Sessions are opaque random tokens**, not signed payloads. The server keeps
  the mapping in its own store, so a session can be revoked instantly (logout)
  and it carries no information a client could tamper with.
* **Three roles**: `student`, `worker` and `admin`. A worker account is bound
  to exactly one technician record (`worker_id`), which is what makes "see only
  my own jobs" enforce itself. An admin account owns no tickets and no route:
  it exists to operate the system — see the whole board, fix a wrong room, reset
  a password, issue a technician account — and is deliberately NOT a super-user
  that can borrow somebody else's console.
* **Everybody lands on one menu.** Sign-in does not choose a console, it opens
  `/menu.html`, and the menu then offers only what the role may open. Identity
  is still enforced per page afterwards.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

# --------------------------------------------------------------------------- #
# 角色 / Roles
# --------------------------------------------------------------------------- #

ROLE_STUDENT = "student"
ROLE_WORKER = "worker"
ROLE_ADMIN = "admin"
ROLES = (ROLE_STUDENT, ROLE_WORKER, ROLE_ADMIN)

#: Where each role lands after signing in. All three land on the SAME main
#: menu — that is the point of it: one front door per person, and the menu then
#: offers only the sections that role may open. The menu itself is
#: role-aware but not role-exclusive, so it is served to anybody signed in.
#:
#: The server decides this. A student cannot reach the technician console by
#: typing its URL, and an administrator cannot reach either of the two
#: consoles, because an admin account is an operator, not a student or a
#: technician in disguise.
MENU_PATH = "/menu.html"

HOME_BY_ROLE = {
    ROLE_STUDENT: MENU_PATH,
    ROLE_WORKER: MENU_PATH,
    ROLE_ADMIN: MENU_PATH,
}

#: Sentinel for "any signed-in account, whatever the role" — used by the pages
#: every role shares (the main menu, the stylesheet, the shared chrome).
ANY_ROLE = "any"

SESSION_COOKIE = "dc_session"
SESSION_DAYS = 7
PBKDF2_ROUNDS = 200_000
MIN_PASSWORD = 6
MAX_PASSWORD = 128
MAX_USERNAME = 32
MAX_NAME = 40
MAX_DORM = 30
MAX_TRADE = 60
MAX_PHONE = 40


# --------------------------------------------------------------------------- #
# 口令 / Passwords
# --------------------------------------------------------------------------- #

def hash_password(password, salt=None, rounds=PBKDF2_ROUNDS):
    """`pbkdf2_sha256$<rounds>$<salt>$<digest>` — self-describing, upgradeable."""
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             salt.encode("utf-8"), rounds)
    return "pbkdf2_sha256$%d$%s$%s" % (rounds, salt, dk.hex())


def verify_password(password, stored):
    """Constant-time check of `password` against a stored digest."""
    if not isinstance(stored, str) or not isinstance(password, str):
        return False
    try:
        algo, rounds, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds)
    except Exception:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             salt.encode("utf-8"), rounds)
    return hmac.compare_digest(dk.hex(), digest)


# --------------------------------------------------------------------------- #
# 会话 / Sessions
# --------------------------------------------------------------------------- #

def new_token():
    """Opaque 256-bit session token (43 URL-safe characters)."""
    return secrets.token_urlsafe(32)


def new_session(user, days=SESSION_DAYS):
    now = datetime.now()
    return {
        "token": new_token(),
        "user_id": user["id"],
        "role": user["role"],
        "username": user["username"],
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(days=days)).isoformat(timespec="seconds"),
    }


def session_expired(session, now=None):
    try:
        expires = datetime.fromisoformat(session["expires_at"])
    except Exception:
        return True
    return (now or datetime.now()) >= expires


# --------------------------------------------------------------------------- #
# 输入校验 / Input validation
#
# Usernames double as the student number / staff number, so they are kept to
# letters + digits + `._-` — that keeps them safe to echo anywhere and stops a
# display name from being smuggled in as a login id.
# --------------------------------------------------------------------------- #

_USERNAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def clean_username(raw):
    return (raw or "").strip()


def username_error(username):
    """Return an error `code` for a bad username, or None when it is fine."""
    if not username:
        return "auth_missing_username"
    if len(username) > MAX_USERNAME:
        return "auth_bad_username"
    for ch in username:
        if ch not in _USERNAME_OK:
            return "auth_bad_username"
    return None


def password_error(password):
    if not password:
        return "auth_missing_password"
    if len(password) < MIN_PASSWORD:
        return "auth_weak_password"
    if len(password) > MAX_PASSWORD:
        return "auth_weak_password"
    return None


def clean_name(raw):
    return (raw or "").strip()[:MAX_NAME]


def normalize_student_phone(raw, required=True):
    """Return ``(normalized, error_code)`` for a mainland mobile number.

    Account phone numbers are deliberately separate from the technician
    roster's ``phone`` (an internal extension).  Spaces and hyphens are the
    only presentation characters accepted, then the stored value is exactly
    eleven digits beginning with 1.
    """
    value = raw if isinstance(raw, str) else ""
    if len(value) > MAX_PHONE:
        return "", "auth_bad_phone"
    value = value.strip()
    if not value:
        return ("", "auth_missing_phone") if required else ("", None)
    if any(not (ch.isdigit() or ch in " -") for ch in value):
        return "", "auth_bad_phone"
    normalized = value.replace(" ", "").replace("-", "")
    if len(normalized) != 11 or not normalized.startswith("1") or not normalized.isdigit():
        return "", "auth_bad_phone"
    return normalized, None


# --------------------------------------------------------------------------- #
# 对外视图 / Public view
# --------------------------------------------------------------------------- #

def public_user(user):
    """The user object safe to hand to the browser — never the digest."""
    if not user:
        return None
    return {
        "id": user["id"],
        "role": user["role"],
        "username": user["username"],
        "name": user.get("name", ""),
        "name_en": user.get("name_en", ""),
        # This view is only returned for the signed-in account itself.  Admin
        # lists use a separate role-gated view; no student list exposes it.
        "phone": user.get("phone", ""),
        "college": user.get("college", ""),
        # The room IS part of who you are: a repair request is filed against
        # the address on the account, so the student console never has to ask
        # for it and the server never has to trust what a browser sends.
        "block": user.get("block", ""),
        "dorm": user.get("dorm", ""),
        "worker_id": user.get("worker_id", ""),
        "worker_ref": user.get("worker_ref", ""),
        "created_at": user.get("created_at", ""),
    }
