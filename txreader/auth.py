"""Single-user login: one password (hash in an env var), session cookies, a login log.

    TXREADER_PASSWORD_HASH   output of `python -m txreader.auth`
    TXREADER_AUTH=off        no login at all (only for running on your own machine)

Run `python -m txreader.auth` to turn a password into a hash.
"""
import base64
import getpass
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from .db import connect, now

COOKIE = "txreader_session"
SESSION_DAYS = 30
MAX_FAILURES = 5                    # consecutive wrong passwords ...
LOCK_WINDOW = timedelta(minutes=15)  # ... within this long lock the login for the same time
_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024}


def auth_enabled() -> bool:
    return os.environ.get("TXREADER_AUTH", "").lower() != "off"


def hash_password(password: str) -> str:
    """'scrypt:n:r:p:salt:hash' (':' rather than '$', which shells and compose files expand)."""
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT["n"], r=_SCRYPT["r"], p=_SCRYPT["p"],
                         maxmem=_SCRYPT["maxmem"])
    b64 = lambda b: base64.b64encode(b).decode()
    return f"scrypt:{_SCRYPT['n']}:{_SCRYPT['r']}:{_SCRYPT['p']}:{b64(salt)}:{b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, key = stored.split(":")
        if algo != "scrypt":
            return False
        key = base64.b64decode(key)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=_SCRYPT["maxmem"], dklen=len(key))
    except ValueError:
        return False
    return hmac.compare_digest(got, key)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _log(conn, ip, user_agent, success, reason):
    conn.execute("INSERT INTO login_events (at, ip, user_agent, success, reason) VALUES (?, ?, ?, ?, ?)",
                 (now(), ip, user_agent, int(success), reason))


def locked_until(conn) -> datetime | None:
    """Locked when the last MAX_FAILURES wrong passwords, with no success since, all fell within LOCK_WINDOW."""
    rows = conn.execute(
        "SELECT at FROM login_events WHERE reason = 'bad_password'"
        " AND id > COALESCE((SELECT MAX(id) FROM login_events WHERE success = 1), 0)"
        " ORDER BY id DESC LIMIT ?", (MAX_FAILURES,)).fetchall()
    if len(rows) < MAX_FAILURES:
        return None
    until = datetime.fromisoformat(rows[-1]["at"]) + LOCK_WINDOW
    return until if until > datetime.now(timezone.utc) else None


class LoginError(Exception):
    pass


def login(password: str, ip: str | None, user_agent: str | None) -> str:
    """Check the password and open a session; returns the cookie value."""
    stored = os.environ.get("TXREADER_PASSWORD_HASH", "")
    error, token = None, None
    # The failure is logged and committed before raising (raising inside would roll it back)
    with connect() as conn:
        if not stored:
            _log(conn, ip, user_agent, False, "not_configured")
            error = "服务器没有设置密码（环境变量 TXREADER_PASSWORD_HASH）"
        elif until := locked_until(conn):
            _log(conn, ip, user_agent, False, "locked")
            mins = max(1, round((until - datetime.now(timezone.utc)).total_seconds() / 60))
            error = f"密码错误次数太多，请 {mins} 分钟后再试"
        elif not verify_password(password, stored):
            _log(conn, ip, user_agent, False, "bad_password")
            error = "密码不对"
        else:
            _log(conn, ip, user_agent, True, "ok")
            token = secrets.token_urlsafe(32)
            created = datetime.now(timezone.utc)
            conn.execute(
                "INSERT INTO sessions (token_hash, created_at, expires_at, ip, user_agent) VALUES (?, ?, ?, ?, ?)",
                (_token_hash(token), created.isoformat(timespec="seconds"),
                 (created + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds"), ip, user_agent))
    if error:
        raise LoginError(error)
    return token


def session_valid(token: str | None) -> bool:
    if not token:
        return False
    with connect() as conn:
        r = conn.execute("SELECT expires_at FROM sessions WHERE token_hash = ?", (_token_hash(token),)).fetchone()
    return bool(r) and datetime.fromisoformat(r["expires_at"]) > datetime.now(timezone.utc)


def logout(token: str | None) -> None:
    if token:
        with connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def logout_all() -> int:
    with connect() as conn:
        return conn.execute("DELETE FROM sessions").rowcount


def security_overview(current_token: str | None) -> dict:
    current = _token_hash(current_token) if current_token else None
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now(),))
        sessions = [{**dict(r), "current": r["token_hash"] == current} for r in conn.execute(
            "SELECT token_hash, created_at, expires_at, ip, user_agent FROM sessions ORDER BY id DESC")]
        events = [dict(r) for r in conn.execute(
            "SELECT at, ip, user_agent, success, reason FROM login_events ORDER BY id DESC LIMIT 100")]
    for s in sessions:
        del s["token_hash"]
    return {"sessions": sessions, "events": events}


if __name__ == "__main__":
    pw = getpass.getpass("新密码：")
    if pw != getpass.getpass("再输一次："):
        raise SystemExit("两次输入不一致")
    print(hash_password(pw))
