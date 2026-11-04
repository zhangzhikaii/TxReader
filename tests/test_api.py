"""The web API end to end, with a fake parser (no OCR) and a throwaway data directory."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

import app as webapp
from txreader import auth, store
from txreader.models import Tx

# Each fake "screenshot" is a key into this table
SCREENS = {
    b"shot1": [Tx(month=10, day=2, amount=-20.0, merchant="超市", year=2026, hour=9),
               Tx(month=10, day=1, amount=-10.0, merchant="便利店", year=2026, hour=12)],
    b"shot2": [Tx(month=10, day=3, amount=-30.0, merchant="饭店", year=2026, hour=19),
               Tx(month=10, day=2, amount=-20.0, merchant="超市", year=2026, hour=9)],
    b"shot3": [Tx(month=10, day=3, amount=-30.0, merchant="饭庄", year=2026, hour=19)],
}
PASSWORD = "correct horse"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TXREADER_DATA", str(tmp_path))
    monkeypatch.setenv("TXREADER_AUTH", "off")
    monkeypatch.setattr(store, "decode_image", lambda data: data)
    monkeypatch.setattr(store, "save_image", lambda img, digest: f"images/{digest}.webp")
    calls = []

    def fake_parse(img, source):
        calls.append(img)
        return list(SCREENS[img]), []
    monkeypatch.setattr(store, "parse_image", fake_parse)
    c = TestClient(webapp.app)
    c.parse_calls = calls
    return c


def upload(client, *shots, source="wechat"):
    files = [("files", (f"{s.decode()}.png", s, "image/png")) for s in shots]
    r = client.post("/api/parse", files=files, data={"year": "2026", "source": source})
    assert r.status_code == 200, r.text
    return r.json()


def test_review_confirm_and_skip_on_next_upload(client):
    res = upload(client, b"shot1")
    assert res["counts"] == {"recognized": 2, "skip": 0, "merge": 0, "suspect": 0, "new": 2}
    pending = res["pending"]
    assert [p["merchant"] for p in pending] == ["便利店", "超市"]  # oldest first

    # Edit a pending row; the edit survives a reload
    first = pending[0]
    assert client.patch(f"/api/pending/{first['id']}", json={"category": "日用品", "merchant": "便利店 A"}).is_success
    again = client.get("/api/pending", params={"source": "wechat"}).json()
    assert again["counts"] == {"wechat": 2}
    assert again["pending"][0]["category"] == "日用品" and again["pending"][0]["merchant"] == "便利店 A"

    # Confirm the first row only
    row = {"id": first["id"], "date": first["date"], "account": "微信", "currency": "人民币",
           "amount": first["amount"], "category": "日用品", "description": "便利店 A"}
    assert client.post("/api/confirm", json={"rows": [row]}).json() == {"confirmed": 1}

    # Overlapping screenshot: 超市 is still pending (merged), 饭店 is new
    res = upload(client, b"shot2")
    assert res["counts"] == {"recognized": 2, "skip": 0, "merge": 1, "suspect": 0, "new": 1}
    # Same file again: not recognized again, everything already known
    res = upload(client, b"shot1")
    assert client.parse_calls == [b"shot1", b"shot2"]
    assert res["counts"]["skip"] == 1 and res["counts"]["merge"] == 1
    assert res["skipped"] == [{"date": "2026/10/01 12:00:00", "amount": -10.0, "merchant": "便利店"}]

    # A look-alike is added but flagged
    res = upload(client, b"shot3")
    assert res["counts"]["suspect"] == 1
    flagged = [p for p in res["pending"] if p["suspect"]]
    assert flagged[0]["merchant"] == "饭庄" and flagged[0]["suspect"]["merchant"] == "饭店"

    # Deleting it removes it from the pending list
    assert client.post("/api/pending/delete", json={"ids": [flagged[0]["id"]]}).json() == {"deleted": 1}
    assert len(client.get("/api/pending", params={"source": "wechat"}).json()["pending"]) == 2


def test_transactions_and_exports(client):
    pending = upload(client, b"shot1")["pending"]
    rows = [{"id": p["id"], "date": p["date"], "account": "微信", "currency": "人民币", "amount": p["amount"],
             "category": "饮食费", "description": p["merchant"]} for p in pending]
    client.post("/api/confirm", json={"rows": rows})
    txs = client.get("/api/transactions").json()["transactions"]
    assert [t["description"] for t in txs] == ["便利店", "超市"]
    assert all(t["export_count"] == 0 and t["source"] == "wechat" for t in txs)

    # Only the description can change after confirming
    assert client.patch(f"/api/transactions/{txs[0]['id']}", json={"description": 'Shop, "A"'}).is_success

    r = client.post("/api/exports", json={"ids": [t["id"] for t in txs], "filename": "out.csv"})
    assert r.content.decode("utf-8") == (
        "﻿日期,曜日,账户,币种,金额,种类,描述\r\n"
        '2026/10/01 12:00:00,木,微信,人民币,-10.00,饮食费,"Shop, ""A"""\r\n'
        "2026/10/02 09:00:00,金,微信,人民币,-20.00,饮食费,超市\r\n")
    client.post("/api/exports", json={"ids": [txs[0]["id"]]})  # exporting again is allowed
    txs = client.get("/api/transactions").json()["transactions"]
    assert [t["export_count"] for t in txs] == [2, 1]
    assert [e["count"] for e in client.get("/api/exports").json()["exports"]] == [1, 2]

    only = client.get("/api/transactions", params={"date_from": "2026-10-02", "date_to": "2026-10-02"}).json()
    assert [t["description"] for t in only["transactions"]] == ["超市"]


def test_confirm_rejects_bad_dates_and_confirmed_rows(client):
    p = upload(client, b"shot1")["pending"][0]
    row = {"id": p["id"], "date": "2026/02/30 00:00:00", "amount": -1}
    assert client.post("/api/confirm", json={"rows": [row]}).status_code == 400
    row["date"] = p["date"]
    assert client.post("/api/confirm", json={"rows": [row]}).is_success
    assert client.post("/api/confirm", json={"rows": [row]}).status_code == 404


def test_login_lockout_and_sessions(client, monkeypatch):
    monkeypatch.setenv("TXREADER_AUTH", "on")
    monkeypatch.setenv("TXREADER_PASSWORD_HASH", auth.hash_password(PASSWORD))
    assert client.get("/api/exports").status_code == 401
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"

    assert client.post("/api/login", json={"password": PASSWORD}).is_success
    assert client.get("/api/exports").is_success
    assert client.post("/api/logout").is_success
    assert client.get("/api/exports").status_code == 401

    for _ in range(auth.MAX_FAILURES):
        assert "密码不对" in client.post("/api/login", json={"password": "nope"}).json()["detail"]
    r = client.post("/api/login", json={"password": PASSWORD})  # right password, but locked
    assert r.status_code == 401 and "分钟后再试" in r.json()["detail"]

    monkeypatch.setattr(auth, "locked_until", lambda conn: None)  # pretend the lock ran out
    assert client.post("/api/login", json={"password": PASSWORD}).is_success
    sec = client.get("/api/security").json()
    assert [e["reason"] for e in sec["events"][:3]] == ["ok", "locked", "bad_password"]
    assert len(sec["sessions"]) == 1 and sec["sessions"][0]["current"]
    client.post("/api/logout-all")
    assert client.get("/api/exports").status_code == 401
