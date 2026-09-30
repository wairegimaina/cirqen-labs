"""Admin sign-in for Cirqen Control: password + authenticator code (TOTP).

Standard library only. Passwords are scrypt hashes. The authenticator code is
RFC 6238 TOTP (the six digits any authenticator app shows); a code is
accepted once, so one seen over someone's shoulder cannot be replayed.
Sessions are random tokens held server-side (only their hash is stored),
sent in an HttpOnly, Secure, SameSite=Strict cookie, and expire after an
hour idle or eight hours in all. Every form carries the session's CSRF token.
Repeated failures lock the username and the address for a while.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote

import control_store as cs

ROLES = ("owner", "support", "finance")
SESSION_COOKIE = "cq_admin"
IDLE_SECONDS = 3600
MAX_SECONDS = 8 * 3600
LOCK_WINDOW = 15 * 60
MAX_FAILS_PER_USER = 5
MAX_FAILS_PER_IP = 20
MIN_PASSWORD = 12


class AuthError(Exception):
    pass


# ── passwords ────────────────────────────────────────────────────────────────

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=32)
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# ── TOTP (RFC 6238, SHA-1, 30 s, 6 digits: what authenticator apps use) ─────

STEP = 30


def new_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def totp_now(secret: str, at: float | None = None) -> str:
    return _code(secret, int((at or time.time()) // STEP))


def verify_totp(admin: dict, code: str, at: float | None = None) -> bool:
    """Accept the current step or one either side (clock drift), never a step
    at or before the last one used."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != 6:
        return False
    current = int((at or time.time()) // STEP)
    for step in (current - 1, current, current + 1):
        if step > admin["totp_last_step"] and hmac.compare_digest(_code(admin["totp_secret"], step), code):
            cs.conn().execute("UPDATE admins SET totp_last_step = ? WHERE id = ?", (step, admin["id"]))
            admin["totp_last_step"] = step
            return True
    return False


def otpauth_uri(username: str, secret: str) -> str:
    return (f"otpauth://totp/Cirqen%20Control:{quote(username)}?secret={secret}"
            "&issuer=Cirqen%20Control&algorithm=SHA1&digits=6&period=30")


# ── admins ───────────────────────────────────────────────────────────────────

def create_admin(username: str, password: str, role: str) -> str:
    """Create an account; returns its TOTP secret (shown once, to set up the app)."""
    username = username.strip().lower()
    if role not in ROLES:
        raise AuthError(f"role must be one of {', '.join(ROLES)}")
    if not username or not username.replace(".", "").replace("-", "").replace("_", "").isalnum():
        raise AuthError("username: letters, digits, dot, dash or underscore")
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"password must be at least {MIN_PASSWORD} characters")
    secret = new_totp_secret()
    try:
        cs.conn().execute(
            "INSERT INTO admins (username, password_hash, totp_secret, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, hash_password(password), secret, role, cs.now()),
        )
    except Exception as exc:
        raise AuthError(f"could not create {username}: {exc}") from exc
    return secret


def get_admin(admin_id: int | None = None, username: str | None = None) -> dict | None:
    if admin_id is not None:
        row = cs.conn().execute("SELECT * FROM admins WHERE id = ?", (admin_id,)).fetchone()
    else:
        row = cs.conn().execute("SELECT * FROM admins WHERE username = ?",
                                ((username or "").strip().lower(),)).fetchone()
    return dict(row) if row else None


def list_admins() -> list[dict]:
    return [dict(r) for r in cs.conn().execute(
        "SELECT id, username, role, active, created_at, last_login_at FROM admins ORDER BY username")]


def count_admins() -> int:
    return cs.conn().execute("SELECT COUNT(*) FROM admins").fetchone()[0]


# ── login ────────────────────────────────────────────────────────────────────

def _recent_failures(column: str, value: str) -> int:
    return cs.conn().execute(
        f"SELECT COUNT(*) FROM login_attempts WHERE {column} = ? AND ok = 0 AND at > ?",
        (value, time.time() - LOCK_WINDOW),
    ).fetchone()[0]


def login(username: str, password: str, code: str, ip: str) -> dict:
    """The admin, or AuthError with one message for every kind of failure."""
    username = (username or "").strip().lower()
    if _recent_failures("username", username) >= MAX_FAILS_PER_USER or \
            _recent_failures("ip", ip) >= MAX_FAILS_PER_IP:
        cs.audit(username or "?", "login_locked", ip=ip)
        raise AuthError("Too many failed attempts. Try again in 15 minutes.")
    admin = get_admin(username=username)
    ok = bool(admin and admin["active"] and check_password(password or "", admin["password_hash"])
              and verify_totp(admin, code))
    cs.conn().execute("INSERT INTO login_attempts (username, ip, at, ok) VALUES (?, ?, ?, ?)",
                      (username, ip, time.time(), int(ok)))
    if not ok:
        cs.audit(username or "?", "login_failed", ip=ip)
        raise AuthError("Wrong username, password or code.")
    cs.conn().execute("UPDATE admins SET last_login_at = ? WHERE id = ?", (cs.now(), admin["id"]))
    cs.audit(username, "login", ip=ip)
    return admin


# ── sessions ─────────────────────────────────────────────────────────────────

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(admin: dict, ip: str) -> str:
    token = secrets.token_urlsafe(32)
    stamp = time.time()
    cs.conn().execute(
        "INSERT INTO sessions (token_hash, admin_id, csrf, created_at, last_seen, ip) VALUES (?, ?, ?, ?, ?, ?)",
        (_hash(token), admin["id"], secrets.token_urlsafe(24), stamp, stamp, ip),
    )
    return token


def session_for(token: str | None) -> tuple[dict, dict] | None:
    """(session, admin) for a live session, refreshing its idle clock."""
    if not token:
        return None
    row = cs.conn().execute("SELECT * FROM sessions WHERE token_hash = ?", (_hash(token),)).fetchone()
    if row is None:
        return None
    session = dict(row)
    stamp = time.time()
    if stamp - session["last_seen"] > IDLE_SECONDS or stamp - session["created_at"] > MAX_SECONDS:
        end_session(token)
        return None
    admin = get_admin(admin_id=session["admin_id"])
    if not admin or not admin["active"]:
        end_session(token)
        return None
    cs.conn().execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (stamp, session["token_hash"]))
    return session, admin


def end_session(token: str | None) -> None:
    if token:
        cs.conn().execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))


def end_all_sessions(admin_id: int) -> None:
    cs.conn().execute("DELETE FROM sessions WHERE admin_id = ?", (admin_id,))
