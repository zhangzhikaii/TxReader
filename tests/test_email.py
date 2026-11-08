"""Email import end to end, with the Microsoft servers faked."""
import sqlite3
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

import app as webapp
from txreader import db, mail

RULE = {"account": "某某卡", "currency": "日元", "sender": "notice@card.example",
        "subject": "ご利用のお知らせ",
        "date_re": r"利用日時\s+([0-9/:\s]+)", "merchant_re": r"利用店舗\s+([^\n]+)",
        "amount_re": r"利用金額\s+([0-9,]+)円"}


def body(date="2026/10/29 12:34", shop="某某商户", amount="1,234円"):
    return f"いつもご利用ありがとうございます。\r\n利用日時　{date}\r\n利用店舗　{shop}\r\n利用金額　{amount}\r\n"


def message(mid, text, sender="notice@card.example", subject="【某某カード】ご利用のお知らせ",
            received="2026-10-29T03:40:00Z"):
    return {"id": mid, "subject": subject, "receivedDateTime": received,
            "from": {"emailAddress": {"address": sender}}, "body": {"contentType": "text", "content": text}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TXREADER_DATA", str(tmp_path))
    monkeypatch.setenv("TXREADER_AUTH", "off")
    token_calls, windows = [], []
    inbox = []

    def fake_token(data):
        token_calls.append(data)
        return {"access_token": f"access-{len(token_calls)}", "refresh_token": f"refresh-{len(token_calls)}",
                "expires_in": 3600}

    def fake_messages(token, since, until):
        windows.append((token, since, until))
        return list(inbox)
    monkeypatch.setattr(mail, "_token_call", fake_token)
    monkeypatch.setattr(mail, "_messages", fake_messages)
    c = TestClient(webapp.app)
    c.token_calls, c.windows, c.inbox = token_calls, windows, inbox
    return c


def connect(client):
    assert client.put("/api/email/credentials", json={"client_id": "cid", "client_secret": "s3cret"}).is_success
    r = client.get("/api/email/authorize", follow_redirects=False)
    assert r.status_code == 303
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["client_id"] == ["cid"] and "Mail.Read" in q["scope"][0]
    assert q["redirect_uri"] == ["http://testserver/api/email/callback"]
    r = client.get("/api/email/callback", params={"code": "the-code", "state": q["state"][0]}, follow_redirects=False)
    assert r.headers["location"] == "/?email_connected=1#settings-email"
    return q


def test_sign_in(client):
    s = client.get("/api/email").json()
    assert s["connected"] is False and s["has_secret"] is False
    assert s["redirect_uri"] == "" and s["default_redirect_uri"] == "http://testserver/api/email/callback"

    # Wrong state: refused, nothing stored
    client.put("/api/email/credentials", json={"client_id": "cid", "client_secret": "s3cret"})
    client.get("/api/email/authorize", follow_redirects=False)
    r = client.get("/api/email/callback", params={"code": "x", "state": "forged"}, follow_redirects=False)
    assert "email_error=" in r.headers["location"]
    assert client.token_calls == []

    connect(client)
    assert client.token_calls[0]["code"] == "the-code" and client.token_calls[0]["client_secret"] == "s3cret"
    s = client.get("/api/email").json()
    assert s["connected"] is True and s["has_secret"] is True
    assert "s3cret" not in str(s) and "access" not in str(s)  # secrets stay on the server

    # Keeping the secret while saving the same id keeps the connection; another app signs out
    client.put("/api/email/credentials", json={"client_id": "cid"})
    assert client.get("/api/email").json()["connected"] is True
    client.put("/api/email/credentials", json={"client_id": "other"})
    s = client.get("/api/email").json()
    assert s["connected"] is False and s["has_secret"] is True


def test_refresh_token_typed_in(client):
    assert client.put("/api/email/token", json={"refresh_token": "pasted"}).status_code == 400  # no app yet
    client.put("/api/email/credentials", json={"client_id": "cid", "client_secret": "s3cret"})
    assert client.put("/api/email/token", json={"refresh_token": " "}).status_code == 400
    assert client.put("/api/email/token", json={"refresh_token": "pasted"}).is_success
    call = client.token_calls[-1]
    assert call["grant_type"] == "refresh_token" and call["refresh_token"] == "pasted"
    assert client.get("/api/email").json()["connected"] is True


def test_redirect_uri_can_be_the_site_root(client):
    assert client.put("/api/email/credentials", json={"client_id": "cid", "redirect_uri": "localhost"}).status_code == 400
    client.put("/api/email/credentials", json={"client_id": "cid", "client_secret": "s",
                                               "redirect_uri": "http://localhost:8000"})
    assert client.get("/api/email").json()["redirect_uri"] == "http://localhost:8000"
    loc = client.get("/api/email/authorize", follow_redirects=False).headers["location"]
    q = parse_qs(urlparse(loc).query)
    assert q["redirect_uri"] == ["http://localhost:8000"]
    # Microsoft sends the browser back to the root, which passes the code on
    r = client.get("/", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)
    assert r.headers["location"].startswith("/api/email/callback?")
    r = client.get(r.headers["location"], follow_redirects=False)
    assert r.headers["location"] == "/?email_connected=1#settings-email"
    assert client.token_calls[-1]["redirect_uri"] == "http://localhost:8000"
    # Blank: back to the default
    client.put("/api/email/credentials", json={"client_id": "cid", "redirect_uri": ""})
    assert client.get("/api/email").json()["redirect_uri"] == ""


def test_rules_are_checked(client):
    bad = client.post("/api/email/rules", json={**RULE, "amount_re": r"利用金額\s+[0-9,]+円"})
    assert bad.status_code == 400 and "括号" in bad.json()["detail"]
    assert client.post("/api/email/rules", json={**RULE, "date_re": "([0-9"}).status_code == 400
    assert client.post("/api/email/rules", json={**RULE, "sender": ""}).status_code == 400
    rid = client.post("/api/email/rules", json=RULE).json()["id"]
    rule = client.get("/api/email").json()["rules"][0]
    assert rule["id"] == rid and rule["category"] == "饮食费"  # the default
    assert client.patch(f"/api/email/rules/{rid}", json={"category": "日用品"}).is_success
    assert client.get("/api/email").json()["rules"][0]["category"] == "日用品"

    t = client.post("/api/email/test", json={**RULE, "text": body()}).json()
    assert t == {"row": {"date": "2026/10/29 12:34:00", "amount": -1234.0, "merchant": "某某商户"}, "problem": None}
    t = client.post("/api/email/test", json={**RULE, "text": "別のメール"}).json()
    assert t["row"] is None and t["problem"] == "没找到日期"


def test_fetch_review_confirm(client):
    assert client.post("/api/email/fetch", json={}).status_code == 400  # no rules yet
    client.post("/api/email/rules", json=RULE)
    r = client.post("/api/email/fetch", json={})
    assert r.status_code == 400 and "连接 Outlook" in r.json()["detail"]
    connect(client)

    client.inbox += [
        message("m1", body()),
        message("m2", body(date="2026/10/30 08:05:09", shop="某某商店", amount="300円"), received="2026-10-30T00:00:00Z"),
        message("m3", "広告です", sender="news@card.example"),         # no rule for this sender
        message("m4", "利用日時　不明", received="2026-10-31T00:00:00Z"),  # the rule can't read it
    ]
    r = client.post("/api/email/fetch", json={"start": "2026-10-01"}).json()
    assert r["counts"] == {"read": 4, "matched": 3, "new": 2, "seen": 0, "failed": 1}
    assert r["failed"][0]["problem"] == "没找到日期"
    pending = r["pending"]
    assert [(p["date"], p["amount"], p["merchant"], p["account"], p["currency"], p["category"]) for p in pending] == [
        ("2026/10/29 12:34:00", -1234.0, "某某商户", "某某卡", "日元", "饮食费"),
        ("2026/10/30 08:05:09", -300.0, "某某商店", "某某卡", "日元", "饮食费")]
    assert pending[0]["image_id"] is None and pending[0]["email_subject"] == "【某某カード】ご利用のお知らせ"
    assert client.get("/api/pending", params={"source": "email"}).json()["counts"] == {"email": 2}
    assert "利用店舗" in client.get(f"/api/emails/{pending[0]['email_id']}").json()["body"]

    # The next update starts where this one ended; emails already read are not added again,
    # even after their pending row was deleted
    first_until = r["until"]
    client.post("/api/pending/delete", json={"ids": [pending[1]["id"]]})
    r = client.post("/api/email/fetch", json={"start": "2020-01-01"}).json()
    assert client.windows[-1][1] == first_until
    assert r["counts"] == {"read": 4, "matched": 3, "new": 0, "seen": 3, "failed": 0}
    assert len(r["pending"]) == 1

    # Confirm: stored as an email transaction
    p = pending[0]
    row = {"id": p["id"], "date": p["date"], "account": p["account"], "currency": p["currency"],
           "amount": p["amount"], "category": "日用品", "description": p["merchant"]}
    assert client.post("/api/confirm", json={"rows": [row]}).json() == {"confirmed": 1}
    txs = client.get("/api/transactions", params={"source": "email"}).json()["transactions"]
    assert [(t["origin"], t["account"], t["amount"], t["category"]) for t in txs] == [("email", "某某卡", -1234.0, "日用品")]
    assert txs[0]["email_id"] == p["email_id"]
    sugg = client.get("/api/suggestions").json()
    assert {"name": "某某卡", "currencies": ["日元"]} in sugg["accounts"]


def test_unrecognized_emails_can_be_retried(client):
    rid = client.post("/api/email/rules", json={**RULE, "amount_re": r"利用金額\s+([0-9,]+)ドル"}).json()["id"]
    connect(client)
    client.inbox.append(message("m1", body()))
    r = client.post("/api/email/fetch", json={}).json()
    assert r["counts"]["failed"] == 1
    assert client.get("/api/email/unrecognized").json()["emails"][0]["problem"] == "没找到金额"

    client.patch(f"/api/email/rules/{rid}", json={"amount_re": RULE["amount_re"]})
    r = client.post("/api/email/retry").json()
    assert (r["new"], r["failed"]) == (1, 0)
    assert r["pending"][0]["amount"] == -1234.0
    assert client.get("/api/email/unrecognized").json()["emails"] == []


def test_expired_token_is_refreshed(client):
    client.post("/api/email/rules", json=RULE)
    connect(client)
    with db.connect() as conn:
        conn.execute("UPDATE config SET value = json_set(value, '$.expires_at', '2020-01-01T00:00:00Z')"
                     " WHERE key = 'email_tokens'")
    client.post("/api/email/fetch", json={})
    refresh = client.token_calls[-1]
    assert refresh["grant_type"] == "refresh_token" and refresh["refresh_token"] == "refresh-1"
    assert client.windows[-1][0] == "access-2"


def test_dates_and_amounts():
    assert mail._date("2026年10月29日") == "2026/10/29 00:00:00"
    assert mail._date("2026/1/2 3:04:05") == "2026/01/02 03:04:05"
    assert mail._date("2026/10/29 12:34\n\n1234") == "2026/10/29 12:34:00"
    assert mail._date("2026/02/30") is None
    assert mail._amount("1,234円") == 1234 and mail._amount("12.50 USD") == 12.5
    assert mail._sender_matches("@card.example", "Notice@Card.Example")
    assert not mail._sender_matches("notice@card.example", "other@card.example")


def test_old_ocr_rows_table_is_upgraded(tmp_path, monkeypatch):
    path = tmp_path / "txreader.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE images (id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, filename TEXT NOT NULL,
            source TEXT NOT NULL, uploaded_at TEXT NOT NULL, path TEXT NOT NULL, parsed TEXT);
        INSERT INTO images VALUES (1, 'h', 'a.png', 'wechat', 't', 'p', NULL);
        CREATE TABLE ocr_rows (id INTEGER PRIMARY KEY, image_id INTEGER NOT NULL REFERENCES images(id),
            pos INTEGER NOT NULL, source TEXT NOT NULL, date TEXT NOT NULL, amount REAL NOT NULL,
            merchant TEXT NOT NULL, balance REAL, sign_guessed INTEGER NOT NULL DEFAULT 0,
            date_guessed INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
            suspect_of INTEGER REFERENCES ocr_rows(id) ON DELETE SET NULL, draft TEXT, created_at TEXT NOT NULL);
        INSERT INTO ocr_rows (id, image_id, pos, source, date, amount, merchant, created_at)
            VALUES (5, 1, 0, 'wechat', '2026/10/01 12:00:00', -10, '超市', 't');
    """)
    old.commit()
    old.close()
    monkeypatch.setenv("TXREADER_DATA", str(tmp_path))
    with db.connect() as conn:
        assert tuple(conn.execute("SELECT id, image_id, email_id, merchant FROM ocr_rows").fetchone()) == (5, 1, None, "超市")
        conn.execute("INSERT INTO ocr_rows (pos, source, date, amount, merchant, created_at)"
                     " VALUES (0, 'email', 'd', -1, 'x', 't')")  # no screenshot needed any more
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'ocr_rows_source'").fetchone()


def test_network_errors_are_retried(monkeypatch):
    calls = []

    def flaky(method, url, **kwargs):
        calls.append(url)
        if len(calls) < 3:
            raise httpx.ConnectTimeout("_ssl.c:1012: The handshake operation timed out")
        return httpx.Response(200, json={"value": [{"id": "m1"}]})
    monkeypatch.setattr(mail.httpx, "request", flaky)
    monkeypatch.setattr(mail.time, "sleep", lambda s: None)
    assert mail._messages("t", "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z") == [{"id": "m1"}]
    assert len(calls) == 3

    calls.clear()
    monkeypatch.setattr(mail.httpx, "request", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectTimeout("timed out")))
    with pytest.raises(mail.MailError, match="试了 3 次"):
        mail._messages("t", "a", "b")
