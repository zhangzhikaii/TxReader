"""Email import: read card / bank notification emails from Outlook (Microsoft Graph) and turn them
into pending rows (source 'email'), reviewed and confirmed like screenshot rows.

Everything is set on the settings page and kept in the database:
    config  email_client_id, email_client_secret   the Azure app registration
            email_redirect_uri  as registered in Azure (blank: the app's /api/email/callback;
                           the site root works too, it passes the code on)
            email_tokens   JSON {access_token, refresh_token, expires_at}
            email_state    the OAuth state while the user is signing in
            email_last_fetch  UTC time the last update read up to; the next one starts there
    email_rules  which emails to read (sender, subject) and three regexes, one group each:

        利用日時　2026/10/29 12:34      date_re      利用日時\\s+([0-9/:\\s]+)
        利用店舗　某某商户               merchant_re  利用店舗\\s+([^\\r\\n]+)
        利用金額　1,234円               amount_re    利用金額\\s+([0-9,]+)円

Every email a rule picked up is kept in `emails` (recognized or not), so reading the same
period again never adds a row twice, even after the pending row was deleted.
"""
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from .db import connect, now, transaction
from .errors import NotFound

AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"
SCOPE = "offline_access Mail.Read"
RULE_FIELDS = ("account", "currency", "category", "sender", "subject", "date_re", "merchant_re", "amount_re")
RULE_REQUIRED = ("account", "currency", "category", "sender", "date_re", "merchant_re", "amount_re")
PATTERNS = {"date_re": "日期", "merchant_re": "描述", "amount_re": "金额"}
DEFAULT_CATEGORY = "饮食费"
MAX_BODY = 50_000
DEFAULT_DAYS = 30  # the first update, when no start date is given
TIMEOUT = httpx.Timeout(30, connect=15)
TRIES = 3  # network errors (a TLS handshake timing out, a dropped connection) are retried


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    """One HTTP call, retried on network errors; raises httpx.TransportError after the last try."""
    for attempt in range(TRIES):
        try:
            return httpx.request(method, url, timeout=TIMEOUT, **kwargs)
        except httpx.TransportError:
            if attempt == TRIES - 1:
                raise
            time.sleep(2 * (attempt + 1))


class MailError(Exception):
    """Something the user has to fix (settings, sign-in) or a failed call to the mail server."""


# ---------- config values ----------

def _get(conn, key: str) -> str | None:
    r = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    return r[0] if r else None


def _set(conn, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM config WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO config (key, value) VALUES (?, ?)"
                     " ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- settings ----------

def redirect_uri(default: str) -> str:
    with connect() as conn:
        return _get(conn, "email_redirect_uri") or default


def settings(default_redirect: str) -> dict:
    """What the settings page shows. Secrets and tokens never leave the server."""
    with connect() as conn:
        rules = [dict(r) for r in conn.execute(
            "SELECT r.*, (SELECT COUNT(*) FROM emails e WHERE e.rule_id = r.id) AS emails"
            " FROM email_rules r ORDER BY r.id")]
        return {
            "client_id": _get(conn, "email_client_id") or "",
            "has_secret": bool(_get(conn, "email_client_secret")),
            "connected": bool(_get(conn, "email_tokens")),
            "last_fetch": _get(conn, "email_last_fetch"),
            "redirect_uri": _get(conn, "email_redirect_uri") or "",
            "default_redirect_uri": default_redirect,
            "rules": rules,
        }


def set_credentials(client_id: str, client_secret: str | None, redirect: str | None = None) -> None:
    """client_secret / redirect None keep the saved ones. Changing the app signs out of it."""
    client_id = (client_id or "").strip()
    if redirect is not None:
        redirect = redirect.strip()
        if redirect and not re.match(r"https?://[^\s/]+(/\S*)?$", redirect):
            raise ValueError(f"回调地址要以 http:// 或 https:// 开头：{redirect}")
    with connect() as conn:
        if redirect is not None:
            _set(conn, "email_redirect_uri", redirect or None)
        if client_id != (_get(conn, "email_client_id") or ""):
            _set(conn, "email_tokens", None)
        _set(conn, "email_client_id", client_id or None)
        if client_secret is not None:
            _set(conn, "email_client_secret", client_secret.strip() or None)


def _credentials(conn) -> tuple[str, str]:
    cid, secret = _get(conn, "email_client_id"), _get(conn, "email_client_secret")
    if not cid or not secret:
        raise MailError("先在「设置 › 邮件」里填写 Client ID 和 Client secret")
    return cid, secret


# ---------- sign-in (OAuth authorization code) ----------

def authorize_url(redirect_uri: str) -> str:
    state = secrets.token_urlsafe(24)
    with connect() as conn:
        cid, _ = _credentials(conn)
        _set(conn, "email_state", state)
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": redirect_uri,
        "response_mode": "query", "scope": SCOPE, "state": state, "prompt": "select_account"})


def _token_call(data: dict) -> dict:
    """POST to the token endpoint; returns the JSON reply or raises MailError."""
    try:
        res = _request("POST", TOKEN_URL, data=data)
    except httpx.TransportError as e:
        raise MailError(f"连不上微软的登录服务器（试了 {TRIES} 次）：{e}") from None
    body = res.json() if "json" in res.headers.get("content-type", "") else {}
    if res.status_code != 200 or not body.get("access_token"):
        detail = body.get("error_description") or body.get("error") or res.text[:200]
        raise MailError(f"微软登录服务器拒绝了：{detail.splitlines()[0] if detail else res.status_code}")
    return body


def _store_tokens(conn, body: dict, old_refresh: str | None = None) -> str:
    tokens = {
        "access_token": body["access_token"],
        # A refresh may or may not come with a new refresh token
        "refresh_token": body.get("refresh_token") or old_refresh,
        "expires_at": _utc(datetime.now(timezone.utc) + timedelta(seconds=int(body.get("expires_in", 3600)))),
    }
    if not tokens["refresh_token"]:
        raise MailError("微软没有返回 refresh token，请确认授权范围里有 offline_access")
    _set(conn, "email_tokens", json.dumps(tokens))
    return tokens["access_token"]


def finish_authorize(code: str, state: str, redirect_uri: str) -> None:
    with connect() as conn:
        expected = _get(conn, "email_state")
        _set(conn, "email_state", None)
        if not expected or not secrets.compare_digest(expected, state or ""):
            raise MailError("登录状态对不上（可能是过期或重复的链接），请重新点「连接 Outlook」")
        cid, secret = _credentials(conn)
    body = _token_call({"client_id": cid, "client_secret": secret, "code": code,
                        "redirect_uri": redirect_uri, "grant_type": "authorization_code", "scope": SCOPE})
    with connect() as conn:
        _store_tokens(conn, body)


def set_refresh_token(refresh_token: str) -> None:
    """A refresh token got elsewhere (instead of signing in here). Tried right away, so a wrong
    one is refused instead of failing at the next update."""
    refresh_token = (refresh_token or "").strip()
    if not refresh_token:
        raise ValueError("请填写 refresh token")
    with connect() as conn:
        cid, secret = _credentials(conn)
    body = _token_call({"client_id": cid, "client_secret": secret, "grant_type": "refresh_token",
                        "refresh_token": refresh_token, "scope": SCOPE})
    with connect() as conn:
        _store_tokens(conn, body, refresh_token)


def disconnect() -> None:
    with connect() as conn:
        _set(conn, "email_tokens", None)


def _access_token() -> str:
    """A valid access token, refreshed when it is about to expire."""
    with connect() as conn:
        raw = _get(conn, "email_tokens")
        if not raw:
            raise MailError("还没有连接 Outlook，请先到「设置 › 邮件」点「连接 Outlook」")
        tokens = json.loads(raw)
        cid, secret = _credentials(conn)
    expires = datetime.strptime(tokens["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if expires - datetime.now(timezone.utc) > timedelta(minutes=5):
        return tokens["access_token"]
    try:
        body = _token_call({"client_id": cid, "client_secret": secret, "grant_type": "refresh_token",
                            "refresh_token": tokens["refresh_token"], "scope": SCOPE})
    except MailError as e:
        raise MailError(f"Outlook 授权过期或失效了，请到「设置 › 邮件」重新连接。（{e}）") from None
    with connect() as conn:
        return _store_tokens(conn, body, tokens["refresh_token"])


# ---------- rules ----------

def _clean_rule(fields: dict, partial: bool) -> dict:
    out = {}
    for k in RULE_FIELDS:
        if k not in fields or fields[k] is None:
            continue
        v = str(fields[k]).strip()
        if k in RULE_REQUIRED and not v:
            raise ValueError("账户、币种、种类、发件人和三个正则都要填")
        if k in PATTERNS:
            try:
                groups = re.compile(v).groups
            except re.error as e:
                raise ValueError(f"{PATTERNS[k]}的正则写错了：{e}") from None
            if groups != 1:
                raise ValueError(f"{PATTERNS[k]}的正则要有且只有一对括号（要取出的部分），现在有 {groups} 对")
        if k == "sender":
            v = v.lower()
        out[k] = v
    if not partial and any(k not in out for k in RULE_REQUIRED):
        raise ValueError("账户、币种、种类、发件人和三个正则都要填")
    return out


def add_rule(fields: dict) -> int:
    rule = _clean_rule({"category": DEFAULT_CATEGORY, **fields}, partial=False)
    with connect() as conn:
        return conn.execute(
            f"INSERT INTO email_rules ({', '.join(rule)}, created_at)"
            f" VALUES ({', '.join('?' * len(rule))}, ?) RETURNING id", (*rule.values(), now())).fetchone()[0]


def update_rule(rule_id: int, fields: dict) -> None:
    rule = _clean_rule(fields, partial=True)
    if not rule:
        return
    with connect() as conn:
        n = conn.execute(f"UPDATE email_rules SET {', '.join(f'{k} = ?' for k in rule)} WHERE id = ?",
                         (*rule.values(), rule_id)).rowcount
    if not n:
        raise NotFound(rule_id)


def delete_rule(rule_id: int) -> None:
    """Emails already read keep their rows; they just no longer point at a rule."""
    with connect() as conn:
        if not conn.execute("DELETE FROM email_rules WHERE id = ?", (rule_id,)).rowcount:
            raise NotFound(rule_id)


def save_settings(credentials: dict | None, add: list[dict], update: dict[int, dict], delete: list[int]) -> None:
    """The settings page's edits, saved together (all or nothing): credentials
    ({"client_id", "client_secret", "redirect_uri"} as set_credentials takes them), new rules,
    {rule id: changed fields}, rules to delete."""
    with transaction():
        if credentials is not None:
            set_credentials(credentials.get("client_id"), credentials.get("client_secret"),
                            credentials.get("redirect_uri"))
        for rule_id in delete:
            delete_rule(rule_id)
        for rule_id, fields in update.items():
            update_rule(rule_id, fields)
        for fields in add:
            add_rule(fields)


# ---------- recognizing one email ----------

def _date(text: str) -> str | None:
    """2026/10/29 12:34, 2026年10月29日 12:34:56, 2026-10-29 ... -> 2026/10/29 12:34:00"""
    m = re.search(r"(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})(?:\D{0,6}?(\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
    if not m:
        return None
    y, mo, d, h, mi, s = (int(v) if v else 0 for v in m.groups())
    try:
        return datetime(y, mo, d, h, mi, s).strftime("%Y/%m/%d %H:%M:%S")
    except ValueError:
        return None


def _amount(text: str) -> float | None:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    return float(m.group().replace(",", "")) if m else None


def recognize(rule: dict, text: str) -> tuple[dict | None, str | None]:
    """({date, amount, merchant}, None), or (None, what didn't match). Amounts are expenses."""
    found = {}
    for k, label in PATTERNS.items():
        m = re.search(rule[k], text)
        if not m or m.group(1) is None:
            return None, f"没找到{label}"
        found[k] = m.group(1).strip()
    date, amount = _date(found["date_re"]), _amount(found["amount_re"])
    if not date:
        return None, f"日期看不懂：「{found['date_re']}」"
    if not amount:
        return None, f"金额看不懂：「{found['amount_re']}」"
    return {"date": date, "amount": -round(amount, 2), "merchant": found["merchant_re"]}, None


def try_rule(fields: dict, text: str) -> dict:
    """The settings page's test box: apply unsaved rule fields to pasted email text."""
    rule = _clean_rule({k: fields.get(k) for k in PATTERNS}, partial=True)
    if any(k not in rule for k in PATTERNS):
        raise ValueError("三个正则都要填")
    row, problem = recognize(rule, text.replace("\r", ""))
    return {"row": row, "problem": problem}


def _sender_matches(rule_sender: str, address: str) -> bool:
    address = address.lower()
    return address.endswith(rule_sender) if rule_sender.startswith("@") else address == rule_sender


def _rule_for(rules: list[dict], msg: dict) -> dict | None:
    address = ((msg.get("from") or {}).get("emailAddress") or {}).get("address") or ""
    subject = (msg.get("subject") or "").lower()
    for rule in rules:
        if _sender_matches(rule["sender"], address) and rule["subject"].lower() in subject:
            return rule
    return None


# ---------- reading the mailbox ----------

def _messages(token: str, since: str, until: str) -> list[dict]:
    """Every message received in [since, until), oldest first, bodies as plain text."""
    params = {
        "$filter": f"receivedDateTime ge {since} and receivedDateTime lt {until}",
        "$orderby": "receivedDateTime",
        "$select": "id,subject,from,receivedDateTime,body",
        "$top": "100",
    }
    headers = {"Authorization": f"Bearer {token}", "Prefer": 'outlook.body-content-type="text"'}
    out, url = [], MESSAGES_URL
    while url:
        try:
            res = _request("GET", url, headers=headers, params=params)
        except httpx.TransportError as e:
            raise MailError(f"连不上 Outlook（试了 {TRIES} 次，可能是网络不稳，过一会儿再点一次）：{e}") from None
        if res.status_code == 401:
            raise MailError("Outlook 拒绝了访问，请到「设置 › 邮件」重新连接")
        if res.status_code != 200:
            try:
                detail = res.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                detail = res.text[:200]
            raise MailError(f"读取邮件失败（{res.status_code}）：{detail}")
        data = res.json()
        out += data.get("value", [])
        url, params = data.get("@odata.nextLink"), None  # the next link carries the query
    return out


def _since(conn, start: str | None) -> str:
    """Where this update starts: the end of the last one, else the given date, else 30 days ago."""
    last = _get(conn, "email_last_fetch")
    if last:
        return last
    if start:
        try:
            day = datetime.strptime(start, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"开始日期格式不对：{start}") from None
        return _utc(day.astimezone())  # midnight, local time
    return _utc(datetime.now(timezone.utc) - timedelta(days=DEFAULT_DAYS))


def fetch(start: str | None = None) -> dict:
    """Read the emails received since the last update and add the recognized ones as pending.

    start (YYYY-MM-DD) is only used for the very first update. Returns
    {"since", "until", "counts": {"read", "matched", "new", "seen", "failed"}, "failed": [...]}.
    """
    with connect() as conn:
        rules = [dict(r) for r in conn.execute("SELECT * FROM email_rules ORDER BY id")]
        if not rules:
            raise MailError("还没有邮件规则，请先到「设置 › 邮件」添加")
        since = _since(conn, start)
    until = _utc(datetime.now(timezone.utc))
    messages = _messages(_access_token(), since, until)

    counts = {"read": len(messages), "matched": 0, "new": 0, "seen": 0, "failed": 0}
    failed = []
    with connect() as conn:
        for msg in messages:
            rule = _rule_for(rules, msg)
            if not rule:
                continue
            counts["matched"] += 1
            if conn.execute("SELECT 1 FROM emails WHERE message_id = ?", (msg["id"],)).fetchone():
                counts["seen"] += 1
                continue
            text = ((msg.get("body") or {}).get("content") or "").replace("\r", "")[:MAX_BODY]
            address = ((msg.get("from") or {}).get("emailAddress") or {}).get("address") or ""
            email_id = conn.execute(
                "INSERT INTO emails (message_id, rule_id, received_at, sender, subject, body, fetched_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (msg["id"], rule["id"], msg.get("receivedDateTime") or "", address,
                 msg.get("subject") or "", text, now())).fetchone()[0]
            problem = _add_row(conn, email_id, rule, text)
            if problem:
                counts["failed"] += 1
                failed.append({"email_id": email_id, "subject": msg.get("subject") or "",
                               "received_at": msg.get("receivedDateTime") or "", "problem": problem})
            else:
                counts["new"] += 1
        _set(conn, "email_last_fetch", until)
    return {"since": since, "until": until, "counts": counts, "failed": failed}


def _add_row(conn, email_id: int, rule: dict, text: str) -> str | None:
    """Recognize one stored email and add it as a pending row; returns the problem if it can't."""
    row, problem = recognize(rule, text)
    if not row:
        return problem
    # The rule's account / currency / category start out as the row's values
    draft = {k: rule[k] for k in ("account", "currency", "category")}
    conn.execute(
        "INSERT INTO ocr_rows (email_id, pos, source, date, amount, merchant, draft, created_at)"
        " VALUES (?, 0, 'email', ?, ?, ?, ?, ?)",
        (email_id, row["date"], row["amount"], row["merchant"], json.dumps(draft, ensure_ascii=False), now()))
    return None


_UNRECOGNIZED = ("SELECT e.id AS email_id, e.subject, e.received_at, e.body, e.rule_id FROM emails e"
                 " WHERE NOT EXISTS (SELECT 1 FROM ocr_rows r WHERE r.email_id = e.id)")


def unrecognized() -> list[dict]:
    """Emails a rule picked up but couldn't read, newest first, with what went wrong now."""
    with connect() as conn:
        rules = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM email_rules")}
        out = []
        for r in conn.execute(_UNRECOGNIZED + " ORDER BY e.received_at DESC"):
            rule = rules.get(r["rule_id"])
            problem = recognize(rule, r["body"])[1] if rule else "规则已删除"
            out.append({"email_id": r["email_id"], "subject": r["subject"], "received_at": r["received_at"],
                        "problem": problem})
    return out


def retry() -> dict:
    """Try the emails that couldn't be read again, with the rules as they are now (after fixing a
    regex). Returns {"new", "failed"}."""
    with connect() as conn:
        rules = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM email_rules")}
        new = failed = 0
        for r in conn.execute(_UNRECOGNIZED).fetchall():
            rule = rules.get(r["rule_id"])
            if rule and not _add_row(conn, r["email_id"], rule, r["body"]):
                new += 1
            else:
                failed += 1
    return {"new": new, "failed": failed}


def email(email_id: int) -> dict:
    with connect() as conn:
        r = conn.execute("SELECT id, received_at, sender, subject, body FROM emails WHERE id = ?",
                         (email_id,)).fetchone()
    if not r:
        raise NotFound(email_id)
    return dict(r)
