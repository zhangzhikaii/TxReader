"""The web API end to end, with a fake parser (no OCR) and a throwaway data directory."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app as webapp
from txreader import auth, store
from txreader.db import connect
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

    # Only the category and description can change after confirming
    tx_url = f"/api/transactions/{txs[0]['id']}"
    assert client.patch(tx_url, json={"description": 'Shop, "A"'}).is_success
    # A category not on the list yet: asked first (409), added once agreed
    r = client.patch(tx_url, json={"category": "日用品"})
    assert r.status_code == 409 and r.json()["detail"]["unknown"]["categories"] == ["日用品"]
    assert client.patch(tx_url, json={"category": "日用品", "add_category": True}).is_success
    assert "日用品" in client.get("/api/suggestions").json()["categories"]
    assert client.patch(tx_url, json={"category": "饮食费"}).is_success  # on the list: no question
    assert client.patch(tx_url, json={"category": "日用品"}).is_success
    assert client.patch(tx_url, json={"amount": 5}).status_code == 400

    r = client.post("/api/exports", json={"ids": [t["id"] for t in txs], "filename": "out.csv"})
    assert r.content.decode("utf-8") == (
        "﻿日期,曜日,账户,币种,金额,种类,描述\r\n"
        '2026/10/01 12:00:00,木,微信,人民币,-10.00,日用品,"Shop, ""A"""\r\n'
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


def test_idle_session_expires(client, monkeypatch):
    monkeypatch.setenv("TXREADER_AUTH", "on")
    monkeypatch.setenv("TXREADER_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("TXREADER_IDLE_MINUTES", "30")
    assert client.post("/api/login", json={"password": PASSWORD}).is_success
    assert client.get("/api/exports").is_success

    def idle_for(minutes):
        with connect() as conn:
            conn.execute("UPDATE sessions SET last_seen_at = ?",
                         ((datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds"),))

    idle_for(20)  # still within the limit, and the request keeps it alive
    assert client.get("/api/exports").is_success
    with connect() as conn:
        seen = datetime.fromisoformat(conn.execute("SELECT last_seen_at FROM sessions").fetchone()[0])
    assert datetime.now(timezone.utc) - seen < timedelta(minutes=1)

    idle_for(31)
    assert client.get("/api/exports").status_code == 401
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0



def test_manual_entry(client):
    row = {"date": "2026/10/05 08:30:15", "account": "现金", "currency": "日元", "amount": -500,
           "category": "饮食费", "description": ""}
    for bad in ({"amount": 0}, {"date": "2026/02/30 00:00:00"}, {"date": "2026/10/05 08:30"},
                {"account": ""}, {"currency": " "}, {"category": ""}):
        assert client.post("/api/transactions", json={"rows": [row, {**row, **bad}]}).status_code == 400
    assert client.get("/api/transactions").json()["transactions"] == []  # all or nothing

    # A new account (and currency) is only added once the user agreed
    rows = [row, {**row, "amount": 1000, "category": "红包"}]
    r = client.post("/api/transactions", json={"rows": rows})
    assert r.status_code == 409
    assert r.json()["detail"]["unknown"] == {"accounts": ["现金"], "currencies": ["日元"], "pairs": [],
                                             "categories": ["饮食费", "红包"]}
    ids = client.post("/api/transactions", json={"rows": rows, "add_accounts": True}).json()["ids"]
    txs = client.get("/api/transactions?source=manual").json()["transactions"]
    assert [(t["origin"], t["date"], t["weekday"], t["amount"], t["image_id"]) for t in txs] == [
        ("manual", "2026/10/05 08:30:15", "月", -500, None), ("manual", "2026/10/05 08:30:15", "月", 1000, None)]
    assert client.get("/api/transactions?source=wechat").json()["transactions"] == []

    # Once stored, a manual row follows the same rules as any other: category and description only
    assert client.patch(f"/api/transactions/{ids[0]}", json={"description": "便利店"}).is_success
    assert client.patch(f"/api/transactions/{ids[0]}", json={"amount": -600}).status_code == 400
    assert client.patch(f"/api/transactions/{ids[0]}", json={"category": ""}).status_code == 400
    assert client.delete(f"/api/transactions/{ids[0]}").status_code == 405

    # An account can hold several currencies; using a known account with a new one asks again
    rmb = {**row, "currency": "人民币"}
    r = client.post("/api/transactions", json={"rows": [rmb]})
    assert r.status_code == 409
    assert r.json()["detail"]["unknown"] == {"accounts": [], "currencies": ["人民币"], "pairs": [["现金", "人民币"]],
                                             "categories": []}
    client.post("/api/transactions", json={"rows": [rmb], "add_accounts": True})
    assert client.post("/api/transactions", json={"rows": [rmb]}).is_success  # known now

    sug = client.get("/api/suggestions").json()
    assert sug["accounts"] == [{"name": "现金", "currencies": ["日元", "人民币"]}]  # 2 uses each
    assert sug["currencies"] == ["日元", "人民币"]  # list order: first added first
    assert sug["categories"] == ["饮食费", "红包"]


def test_account_lists_filled_from_screenshots(client):
    p = upload(client, b"shot3")["pending"][0]
    client.post("/api/confirm", json={"rows": [{"id": p["id"], "date": p["date"], "amount": p["amount"],
                                                "account": "微信", "currency": "人民币"}]})
    sug = client.get("/api/suggestions").json()
    assert sug["accounts"] == [{"name": "微信", "currencies": ["人民币"]}]
    assert sug["categories"] == []  # blank names aren't listed


def test_old_transactions_table_is_upgraded(tmp_path, monkeypatch):
    import sqlite3
    from txreader import db
    path = tmp_path / "txreader.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, ocr_row_id INTEGER NOT NULL UNIQUE,
            date TEXT NOT NULL, weekday TEXT NOT NULL, account TEXT NOT NULL, currency TEXT NOT NULL,
            amount REAL NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL,
            confirmed_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO transactions VALUES (7, 3, '2026/10/01 12:00:00', '木', '微信', '人民币', -10, '饮食费', 'x', 't', 't');
        CREATE TABLE export_items (export_id INTEGER, transaction_id INTEGER REFERENCES transactions(id));
        INSERT INTO export_items VALUES (1, 7);
    """)
    old.commit()
    old.close()
    monkeypatch.setenv("TXREADER_DATA", str(tmp_path))
    with db.connect() as conn:
        r = conn.execute("SELECT id, origin, ocr_row_id FROM transactions").fetchone()
        assert tuple(r) == (7, "screenshot", 3)
        assert conn.execute("SELECT transaction_id FROM export_items").fetchone()[0] == 7
        # The account list starts from the accounts already used
        assert tuple(conn.execute("SELECT a.name, c.name FROM account_currencies ac JOIN accounts a ON a.id = ac.account_id"
                            " JOIN currencies c ON c.id = ac.currency_id").fetchone()) == ("微信", "人民币")
        conn.execute("INSERT INTO transactions (origin, date, weekday, account, currency, amount, category,"
                     " description, confirmed_at, updated_at) VALUES ('manual', 'd', 'w', '', '', 1, '', '', 't', 't')")


def test_settings_lists(client):
    row = {"date": "2026/10/05 08:30:00", "account": "现金", "currency": "日元", "amount": -500,
           "category": "饮食费", "description": ""}
    client.post("/api/transactions", json={"rows": [row], "add_accounts": True})
    get = lambda: client.get("/api/settings").json()
    st = get()
    cash, = st["accounts"]
    assert (cash["name"], cash["used"], cash["currencies"]) == ("现金", 1, [{"name": "日元", "used": 1}])

    # Adding, and no duplicates
    assert client.post("/api/settings/currencies", json={"name": "人民币"}).is_success
    assert client.post("/api/settings/currencies", json={"name": "人民币"}).status_code == 400
    assert client.post("/api/settings/categories", json={"name": " "}).status_code == 400
    rmb = next(c for c in get()["currencies"] if c["name"] == "人民币")
    assert rmb["is_base"]  # 人民币 is the default base currency

    # An account's currencies: add one; a used one can't be removed
    assert client.post(f"/api/settings/accounts/{cash['id']}/currencies", json={"name": "人民币"}).is_success
    assert client.post(f"/api/settings/accounts/{cash['id']}/currencies", json={"name": "美元"}).status_code == 400
    assert client.delete(f"/api/settings/accounts/{cash['id']}/currencies/日元").status_code == 400
    assert client.delete(f"/api/settings/accounts/{cash['id']}/currencies/人民币").is_success

    # Renaming renames the transactions; can't take a name already used
    food = get()["categories"][0]
    assert client.patch(f"/api/settings/categories/{food['id']}", json={"name": "餐饮"}).is_success
    assert client.get("/api/transactions").json()["transactions"][0]["category"] == "餐饮"
    other = client.post("/api/settings/categories", json={"name": "交通费"}).json()["id"]
    assert client.patch(f"/api/settings/categories/{other}", json={"name": "餐饮"}).status_code == 400

    # Order; the base currency stays 人民币 (it can't be changed)
    assert client.post("/api/settings/categories/order", json={"ids": [other]}).is_success
    assert [c["name"] for c in get()["categories"]] == ["交通费", "餐饮"]
    yen = next(c for c in get()["currencies"] if c["name"] == "日元")
    client.patch(f"/api/settings/currencies/{yen['id']}", json={"is_base": True})
    assert [c["name"] for c in get()["currencies"] if c["is_base"]] == ["人民币"]

    # Only unused items can be deleted
    assert client.delete(f"/api/settings/categories/{food['id']}").status_code == 400
    assert client.delete(f"/api/settings/categories/{other}").is_success
    assert client.delete(f"/api/settings/currencies/{yen['id']}").status_code == 400  # used
    assert client.delete(f"/api/settings/currencies/{rmb['id']}").status_code == 400  # the base currency
    assert client.delete("/api/settings/categories/999").status_code == 404
    assert client.get("/api/settings/nope").status_code in (404, 405)


def test_dropped_archived_column(tmp_path, monkeypatch):
    import sqlite3
    from txreader import db
    old = sqlite3.connect(tmp_path / "txreader.db")
    old.executescript("""
        CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, sort INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        INSERT INTO accounts (name, sort, archived, created_at) VALUES ('现金', 1, 1, 't');
    """)
    old.commit()
    old.close()
    monkeypatch.setenv("TXREADER_DATA", str(tmp_path))
    with db.connect() as conn:
        assert "archived" not in {r["name"] for r in conn.execute("PRAGMA table_info(accounts)")}
        assert conn.execute("SELECT name FROM accounts").fetchone()[0] == "现金"


def test_opening_balances_and_rates(client):
    row = {"date": "2026/10/05 08:30:00", "account": "现金", "currency": "日元", "amount": -500,
           "category": "饮食费", "description": ""}
    client.post("/api/transactions", json={"rows": [row, {**row, "currency": "人民币"}], "add_accounts": True})
    st = client.get("/api/settings").json()
    cash = st["accounts"][0]["id"]
    bank = client.post("/api/settings/accounts", json={"name": "某银行"}).json()["id"]
    client.post(f"/api/settings/accounts/{bank}/currencies", json={"name": "人民币"})

    # Start month and opening balances (only for an account's own currencies)
    assert client.put("/api/opening/start-month", json={"month": "2026-13"}).status_code == 400
    assert client.put("/api/opening/start-month", json={"month": "2026-09"}).is_success
    assert client.put(f"/api/opening/{cash}/日元", json={"amount": 12000}).is_success
    assert client.put(f"/api/opening/{bank}/人民币", json={"amount": -50.5}).is_success  # e.g. a credit card
    assert client.put(f"/api/opening/{bank}/日元", json={"amount": 1}).status_code == 400
    assert client.put(f"/api/opening/{bank}/美元", json={"amount": 1}).status_code == 404
    op = client.get("/api/opening").json()
    assert op["start_month"] == "2026-09"
    assert op["currencies"] == ["日元", "人民币"]
    assert [(a["name"], a["balances"]) for a in op["accounts"]] == [
        ("现金", {"日元": 12000, "人民币": None}), ("某银行", {"人民币": -50.5})]

    # What has an opening balance can't be deleted or dropped from the account
    assert client.delete(f"/api/settings/accounts/{bank}").status_code == 400
    assert client.delete(f"/api/settings/accounts/{cash}/currencies/日元").status_code == 400
    assert client.put(f"/api/opening/{bank}/人民币", json={"amount": None}).is_success
    assert client.delete(f"/api/settings/accounts/{bank}").is_success

    # Monthly rates: from the start month to this month; the base currency isn't listed
    r = client.get("/api/rates").json()
    assert r["base"] == "人民币" and r["currencies"] == ["日元"]
    assert r["months"][-2:] == ["2026-10", "2026-09"]
    assert client.put("/api/rates/2026-10/日元", json={"rate": 0.0465}).is_success
    assert client.put("/api/rates/2026-10/日元", json={"rate": 0}).status_code == 400
    assert client.put("/api/rates/2026-10/人民币", json={"rate": 1}).status_code == 400
    assert client.put("/api/rates/2026-1/日元", json={"rate": 1}).status_code == 400
    assert client.get("/api/rates").json()["rates"] == {"2026-10": {"日元": 0.0465}}

    # A currency with rates can't be deleted either
    usd = client.post("/api/settings/currencies", json={"name": "美元"}).json()["id"]
    client.put("/api/rates/2026-10/美元", json={"rate": 7.1})
    assert client.delete(f"/api/settings/currencies/{usd}").status_code == 400
    client.put("/api/rates/2026-10/美元", json={"rate": None})
    assert client.delete(f"/api/settings/currencies/{usd}").is_success


def test_rate_months():
    from txreader import books
    assert books.months_between("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert books._shift("2026-01", -1) == "2025-12"


def test_balance_sheet(client):
    from datetime import date
    from txreader import books
    tx = lambda date_, account, currency, amount: {"date": date_, "account": account, "currency": currency,
                                                   "amount": amount, "category": "饮食费", "description": ""}
    assert client.get("/api/balances").json()["month"] is None  # no start month yet

    client.post("/api/transactions", json={"add_accounts": True, "rows": [
        tx("2026/09/20 10:00:00", "现金", "日元", -300),     # in the start month: already in the opening balance
        tx("2026/10/05 10:00:00", "现金", "日元", -1000),
        tx("2026/10/06 10:00:00", "某银行", "人民币", 0.5),
    ]})
    ids = {a["name"]: a["id"] for a in client.get("/api/settings").json()["accounts"]}
    client.put("/api/opening/start-month", json={"month": "2026-09"})
    client.put(f"/api/opening/{ids['现金']}/日元", json={"amount": 10000})
    client.put(f"/api/opening/{ids['某银行']}/人民币", json={"amount": 400})
    client.put("/api/rates/2026-09/日元", json={"rate": 0.04})
    client.put("/api/rates/2026-10/日元", json={"rate": 0.05})

    sep = books.balances("2026-09", today=date(2026, 10, 20))
    assert sep["months"] == ["2026-10", "2026-09"]
    assert [(a["name"], a["balances"]) for a in sep["accounts"]] == [
        ("现金", {"日元": 10000}), ("某银行", {"人民币": 400})]
    assert sep["currencies"] == ["日元", "人民币"]
    assert (sep["converted"], sep["total"]) == ({"日元": 400, "人民币": 400}, 800)
    assert sep["fx_gain_total"] is None and sep["change"] is None  # nothing before the start month

    # Last month 10,000 yen at 0.04 (400); spent 1,000; now 9,000 at 0.05 (450)
    octo = books.balances("2026-10", today=date(2026, 10, 20))
    assert octo["totals"] == {"日元": 9000, "人民币": 400.5}
    assert octo["rates"] == {"日元": 0.05, "人民币": 1}
    assert (octo["converted"], octo["total"]) == ({"日元": 450, "人民币": 400.5}, 850.5)
    assert octo["fx_gain"] == {"日元": 100, "人民币": 0}  # 10,000 x (0.05 - 0.04)
    assert (octo["fx_gain_total"], octo["prev_total"], octo["change"]) == (100, 800, 50.5)
    # change = this month's flows at this month's rate + FX gain: -1000 x 0.05 + 0.5 + 100
    assert octo["change"] == round(-1000 * 0.05 + 0.5 + octo["fx_gain_total"], 2)

    # A month without a rate: what can't be converted stays empty
    nov = client.get("/api/balances?month=2026-11").json()
    assert nov["converted"]["日元"] is None and nov["total"] is None and nov["change"] is None
    assert nov["fx_gain"]["日元"] is None
    assert client.get("/api/balances?month=2026-08").status_code == 400


def test_transaction_filters(client):
    row = {"date": "2026/10/05 08:30:00", "account": "现金", "currency": "日元", "amount": -500,
           "category": "饮食费", "description": ""}
    client.post("/api/transactions", json={"add_accounts": True, "rows": [
        row, {**row, "account": "微信", "currency": "人民币"}, {**row, "category": "交通费", "date": "2026/11/01 09:00:00"}]})
    get = lambda q: [(t["account"], t["category"]) for t in client.get("/api/transactions?" + q).json()["transactions"]]
    assert get("account=现金") == [("现金", "饮食费"), ("现金", "交通费")]
    assert get("currency=人民币") == [("微信", "饮食费")]
    assert get("category=交通费") == [("现金", "交通费")]
    assert get("account=现金&category=饮食费&date_from=2026-10-01&date_to=2026-10-31") == [("现金", "饮食费")]
