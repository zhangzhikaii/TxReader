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


def test_transactions_and_export(client):
    pending = upload(client, b"shot1")["pending"]
    rows = [{"id": p["id"], "date": p["date"], "account": "微信", "currency": "人民币", "amount": p["amount"],
             "category": "饮食费", "description": p["merchant"]} for p in pending]
    client.post("/api/confirm", json={"rows": rows})
    txs = client.get("/api/transactions").json()["transactions"]
    assert [t["description"] for t in txs] == ["便利店", "超市"]
    assert all(t["source"] == "wechat" for t in txs)

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

    # Several edits together (the transactions page's 保存): all or none
    a, b = txs[0]["id"], txs[1]["id"]
    batch = lambda edits, **kw: client.patch("/api/transactions", json={"edits": edits, **kw})
    r = batch([{"id": a, "description": "改了"}, {"id": b, "category": "新种类甲"}, {"id": a, "category": "新种类乙"}])
    assert r.status_code == 409 and r.json()["detail"]["unknown"]["categories"] == ["新种类甲", "新种类乙"]
    assert batch([{"id": a, "description": "改了"}, {"id": 99999, "description": "x"}]).status_code == 404
    assert batch([{"id": a, "description": "改了"}, {"id": b, "category": ""}]).status_code == 400
    assert [t["description"] for t in client.get("/api/transactions").json()["transactions"]] == ['Shop, "A"', "超市"]
    assert batch([{"id": a, "description": "改了"}, {"id": b, "category": "新种类甲"}], add_categories=True).is_success
    got = {t["id"]: t for t in client.get("/api/transactions").json()["transactions"]}
    assert (got[a]["description"], got[a]["category"], got[b]["category"]) == ("改了", "日用品", "新种类甲")
    assert "新种类甲" in client.get("/api/suggestions").json()["categories"]
    assert batch([{"id": a, "description": 'Shop, "A"'}, {"id": b, "category": "饮食费"}]).is_success

    r = client.get("/api/transactions/export")
    assert r.headers["content-type"].startswith("text/csv")
    assert r.content.decode("utf-8") == (
        "﻿日期,曜日,账户,币种,金额,种类,描述\r\n"
        '2026/10/01 12:00:00,木,微信,人民币,-10.00,日用品,"Shop, ""A"""\r\n'
        "2026/10/02 09:00:00,金,微信,人民币,-20.00,饮食费,超市\r\n")

    # The export uses the same filters as the list
    only = {"date_from": "2026-10-02", "date_to": "2026-10-02"}
    assert [t["description"] for t in client.get("/api/transactions", params=only).json()["transactions"]] == ["超市"]
    assert client.get("/api/transactions/export", params=only).content.decode("utf-8").splitlines()[1:] == [
        "2026/10/02 09:00:00,金,微信,人民币,-20.00,饮食费,超市"]
    # Manual entries are exported too
    client.post("/api/transactions", json={"rows": [{"date": "2026/10/03 08:00:00", "account": "微信", "currency": "人民币",
                                                     "amount": -5, "category": "饮食费", "description": "手填"}]})
    assert client.get("/api/transactions/export", params={"source": "manual"}).content.decode("utf-8").splitlines()[1:] == [
        "2026/10/03 08:00:00,土,微信,人民币,-5.00,饮食费,手填"]


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
    assert client.get("/api/settings").status_code == 401
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"

    assert client.post("/api/login", json={"password": PASSWORD}).is_success
    assert client.get("/api/settings").is_success
    assert client.post("/api/logout").is_success
    assert client.get("/api/settings").status_code == 401

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
    assert client.get("/api/settings").status_code == 401


def test_idle_session_expires(client, monkeypatch):
    monkeypatch.setenv("TXREADER_AUTH", "on")
    monkeypatch.setenv("TXREADER_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("TXREADER_IDLE_MINUTES", "30")
    assert client.post("/api/login", json={"password": PASSWORD}).is_success
    assert client.get("/api/settings").is_success

    def idle_for(minutes):
        with connect() as conn:
            conn.execute("UPDATE sessions SET last_seen_at = ?",
                         ((datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds"),))

    idle_for(20)  # still within the limit, and the request keeps it alive
    assert client.get("/api/settings").is_success
    with connect() as conn:
        seen = datetime.fromisoformat(conn.execute("SELECT last_seen_at FROM sessions").fetchone()[0])
    assert datetime.now(timezone.utc) - seen < timedelta(minutes=1)

    idle_for(31)
    assert client.get("/api/settings").status_code == 401
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
        # Export records were dropped
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name IN ('exports', 'export_items')").fetchone()
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


def test_settings_batch_is_all_or_nothing(client):
    client.post("/api/transactions", json={"add_accounts": True, "rows": [
        {"date": "2026/10/05 08:30:00", "account": "现金", "currency": "日元", "amount": -500,
         "category": "饮食费", "description": ""}]})
    lists = lambda: client.get("/api/settings").json()
    cash = lists()["accounts"][0]["id"]
    food = lists()["categories"][0]["id"]

    # The last edit fails (renaming to a name that's taken), so none of them is saved
    r = client.post("/api/settings/batch", json={"ops": [
        {"op": "add", "kind": "accounts", "name": "某银行"},
        {"op": "rename", "kind": "categories", "id": food, "name": "餐饮"},
        {"op": "add", "kind": "categories", "name": "交通费"},
        {"op": "rename", "kind": "categories", "id": food, "name": "交通费"}]})
    assert r.status_code == 400 and "同名" in r.json()["detail"]
    assert [a["name"] for a in lists()["accounts"]] == ["现金"]
    assert [c["name"] for c in lists()["categories"]] == ["饮食费"]
    assert client.get("/api/transactions").json()["transactions"][0]["category"] == "饮食费"

    assert client.post("/api/settings/batch", json={"ops": [
        {"op": "add", "kind": "currencies", "name": "人民币"},
        {"op": "add_currency", "id": cash, "name": "人民币"},
        {"op": "rename", "kind": "categories", "id": food, "name": "餐饮"},
        {"op": "add", "kind": "categories", "name": "交通费"}]}).is_success
    assert [c["name"] for c in lists()["accounts"][0]["currencies"]] == ["日元", "人民币"]
    assert [c["name"] for c in lists()["categories"]] == ["餐饮", "交通费"]
    assert client.get("/api/transactions").json()["transactions"][0]["category"] == "餐饮"
    assert client.post("/api/settings/batch", json={"ops": [{"op": "drop", "id": cash}]}).status_code == 400

    # Opening balances and rates: also all or nothing
    r = client.put("/api/opening", json={"start_month": "2026-09", "balances": [
        {"account_id": cash, "currency": "日元", "amount": 1000},
        {"account_id": cash, "currency": "美元", "amount": 5}]})  # the account has no 美元
    assert r.status_code >= 400
    op = client.get("/api/opening").json()
    assert op["start_month"] is None and op["accounts"][0]["balances"]["日元"] is None
    assert client.put("/api/opening", json={"start_month": "2026-09", "balances": [
        {"account_id": cash, "currency": "日元", "amount": 1000}]}).is_success
    assert client.put("/api/opening", json={"balances": []}).is_success  # start month left as it is
    op = client.get("/api/opening").json()
    assert op["start_month"] == "2026-09" and op["accounts"][0]["balances"]["日元"] == 1000

    r = client.put("/api/rates", json={"rates": [{"month": "2026-09", "currency": "日元", "rate": 0.05},
                                                 {"month": "2026-10", "currency": "日元", "rate": -1}]})
    assert r.status_code == 400 and client.get("/api/rates").json()["rates"] == {}
    assert client.put("/api/rates", json={"rates": [{"month": "2026-09", "currency": "日元", "rate": 0.05},
                                                    {"month": "2026-10", "currency": "日元", "rate": 0.048}]}).is_success
    assert client.get("/api/rates").json()["rates"] == {"2026-09": {"日元": 0.05}, "2026-10": {"日元": 0.048}}


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


def test_old_rates_and_currencies_are_upgraded(tmp_path, monkeypatch):
    """Currencies gain a code guessed from the name; rates already there count as typed in."""
    import sqlite3
    from txreader import db
    old = sqlite3.connect(tmp_path / "txreader.db")
    old.executescript("""
        CREATE TABLE currencies (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, sort INTEGER NOT NULL DEFAULT 0,
            is_base INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        INSERT INTO currencies (name, sort, is_base, created_at) VALUES ('人民币', 1, 1, 't'), ('日币', 2, 0, 't'), ('某某币', 3, 0, 't');
        CREATE TABLE fx_rates (month TEXT NOT NULL, currency_id INTEGER NOT NULL, rate REAL NOT NULL, PRIMARY KEY (month, currency_id));
        INSERT INTO fx_rates VALUES ('2026-10', 2, 0.047);
    """)
    old.commit()
    old.close()
    monkeypatch.setenv("TXREADER_DATA", str(tmp_path))
    with db.connect() as conn:
        assert [tuple(r) for r in conn.execute("SELECT name, code FROM currencies ORDER BY id")] == [
            ("人民币", "CNY"), ("日币", "JPY"), ("某某币", "")]
        assert tuple(conn.execute("SELECT rate, source, rate_at FROM fx_rates").fetchone()) == (0.047, "manual", None)


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
    # Transactions this month per account, for hiding the quiet ones on the page
    assert [(a["name"], a["count"]) for a in octo["accounts"]] == [("现金", 1), ("某银行", 1)]
    assert [a["count"] for a in sep["accounts"]] == [1, 0]
    # change = this month's flows at this month's rate + FX gain: -1000 x 0.05 + 0.5 + 100
    assert octo["change"] == round(-1000 * 0.05 + 0.5 + octo["fx_gain_total"], 2)

    # Account types: subtotals by type, unset ones last
    assert [(g["type"], g["total"]) for g in octo["groups"]] == [("", 850.5)]
    assert client.post("/api/settings/batch", json={"ops": [
        {"op": "set_type", "kind": "accounts", "id": ids["某银行"], "type": "liquid"},
        {"op": "set_type", "kind": "accounts", "id": ids["现金"], "type": "nope"}]}).status_code == 400
    assert {a["type"] for a in client.get("/api/settings").json()["accounts"]} == {""}  # all or nothing
    assert client.post("/api/settings/batch", json={"ops": [
        {"op": "set_type", "kind": "categories", "id": 1, "type": "liquid"}]}).status_code == 400
    client.post("/api/settings/batch", json={"ops": [
        {"op": "set_type", "kind": "accounts", "id": ids["某银行"], "type": "liquid"},
        {"op": "set_type", "kind": "accounts", "id": ids["现金"], "type": "invest"}]})
    settings = client.get("/api/settings").json()
    assert [(a["name"], a["type"]) for a in settings["accounts"]] == [("现金", "invest"), ("某银行", "liquid")]
    assert settings["account_types"][0] == {"key": "liquid", "label": "活期"}
    octo = books.balances("2026-10", today=date(2026, 10, 20))
    assert [(a["name"], a["type"]) for a in octo["accounts"]] == [("现金", "invest"), ("某银行", "liquid")]
    assert [(g["label"], g["totals"], g["total"]) for g in octo["groups"]] == [
        ("活期", {"日元": 0, "人民币": 400.5}, 400.5), ("投资", {"日元": 9000, "人民币": 0}, 450)]
    client.post("/api/settings/batch", json={"ops": [
        {"op": "set_type", "kind": "accounts", "id": ids["现金"], "type": ""}]})
    assert [a["type"] for a in client.get("/api/settings").json()["accounts"]] == ["", "liquid"]

    # A month without a rate: what can't be converted stays empty
    nov = client.get("/api/balances?month=2026-11").json()
    assert nov["converted"]["日元"] is None and nov["total"] is None and nov["change"] is None
    assert nov["fx_gain"]["日元"] is None
    assert [(g["label"], g["total"]) for g in nov["groups"]] == [("活期", 400.5), ("未分类", None)]
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


def test_monthly_report(client):
    from datetime import date
    from txreader import books
    tx = lambda date_, account, currency, amount, category: {
        "date": date_, "account": account, "currency": currency, "amount": amount, "category": category,
        "description": ""}
    client.post("/api/transactions", json={"add_accounts": True, "rows": [
        tx("2026/09/20 10:00:00", "现金", "日元", -300, "饮食费"),
        tx("2026/10/05 10:00:00", "现金", "日元", -1000, "饮食费"),
        tx("2026/10/06 10:00:00", "某银行", "人民币", 0.5, "红包"),
        # An internal transfer in one currency nets to zero
        tx("2026/10/07 10:00:00", "某银行", "人民币", -100, "内部转账"),
        tx("2026/10/07 10:00:00", "现金", "人民币", 100, "内部转账"),
    ]})
    ids = {a["name"]: a["id"] for a in client.get("/api/settings").json()["accounts"]}

    # Without a start month there is no FX gain, but the flows are there
    m = books.monthly("2026-10", today=date(2026, 10, 20))
    assert m["months"] == ["2026-10", "2026-09"]
    assert m["fx_gain"] is None and "起始月" in m["fx_note"] and m["check"] is None

    client.put("/api/opening/start-month", json={"month": "2026-09"})
    client.put(f"/api/opening/{ids['现金']}/日元", json={"amount": 10000})
    client.put(f"/api/opening/{ids['某银行']}/人民币", json={"amount": 400})
    client.put("/api/rates/2026-09/日元", json={"rate": 0.04})
    client.put("/api/rates/2026-10/日元", json={"rate": 0.05})

    m = books.monthly("2026-10", today=date(2026, 10, 20))
    assert m["currencies"] == ["日元", "人民币"]
    assert [(c["name"], c["amounts"], c["converted"], c["count"]) for c in m["categories"]] == [
        ("饮食费", {"日元": -1000}, -50, 1), ("红包", {"人民币": 0.5}, 0.5, 1), ("内部转账", {"人民币": 0}, 0, 2)]
    assert m["totals"] == {"日元": -1000, "人民币": 0.5}
    assert (m["converted"], m["converted_total"]) == ({"日元": -50, "人民币": 0.5}, -49.5)
    assert (m["fx_gain"], m["fx_gain_total"]) == ({"日元": 100, "人民币": 0}, 100)  # 10,000 x (0.05 - 0.04)
    assert (m["base_total"], m["base_grand_total"]) == ({"日元": 50, "人民币": 0.5}, 50.5)
    # The two reports agree: converted flows + FX gain = the balance sheet's change
    assert m["balance_change"] == 50.5 and m["check"] == "ok"

    sep = client.get("/api/monthly?month=2026-09").json()
    assert sep["categories"][0]["amounts"] == {"日元": -300}
    assert sep["fx_gain"] is None and sep["check"] is None
    nov = client.get("/api/monthly?month=2026-11").json()  # no rate yet
    assert nov["converted_total"] == 0 and nov["fx_gain"]["日元"] is None and nov["base_grand_total"] is None
    assert client.get("/api/monthly?month=2026-1").status_code == 400

    # A year adds up its months, each at its own rate; FX gain and the check from after the start month
    y = books.monthly(year="2026", today=date(2026, 10, 20))
    assert y["period"] == "year" and y["years"] == ["2026"]
    assert [(c["name"], c["amounts"], c["converted"], c["count"]) for c in y["categories"][:1]] == [
        ("饮食费", {"日元": -1300}, -62, 2)]  # -300 x 0.04 - 1000 x 0.05
    assert (y["totals"], y["converted_total"]) == ({"日元": -1300, "人民币": 0.5}, -61.5)
    assert (y["fx_gain_total"], y["base_grand_total"]) == (100, 38.5) and "2026-10" in y["fx_note"]
    assert (y["check"], y["check_total"], y["balance_change"]) == ("ok", 50.5, 50.5)
    client.put("/api/rates/2026-09/日元", json={"rate": None})
    y = books.monthly(year="2026", today=date(2026, 10, 20))
    assert y["missing_rates"] == {"日元": ["2026-09"]} and y["converted_total"] is None and y["check"] is None
    assert client.get("/api/monthly?year=2025").status_code == 400
    assert client.get("/api/monthly?year=26").status_code == 400


def test_trend(client):
    from datetime import date
    from txreader import books
    tx = lambda date_, currency, amount, category: {
        "date": date_, "account": "现金", "currency": currency, "amount": amount, "category": category,
        "description": ""}
    rows = []
    for m in ["07", "08", "09", "10"]:
        rows.append(tx(f"2026/{m}/01 10:00:00", "日元", -50000, "房租"))
        rows.append(tx(f"2026/{m}/25 10:00:00", "人民币", 8000, "工资"))
    for m, v in [("07", -3000), ("08", -5000), ("10", -4000)]:
        rows.append(tx(f"2026/{m}/10 10:00:00", "日元", v, "饮食费"))
    rows += [tx("2026/09/16 10:00:00", "人民币", -100, "内部转账"),     # left out
             tx("2026/11/01 10:00:00", "日元", -50000, "房租")]         # the month in progress
    client.post("/api/transactions", json={"add_accounts": True, "rows": rows})
    client.post("/api/settings/batch", json={"ops": [{"op": "add", "kind": "categories", "name": "交通费"}]})
    today = date(2026, 11, 5)

    t = books.trend(currency="日元", today=today)
    assert t["end"] == "2026-11" and t["current"] == "2026-11"
    assert t["months"] == ["2026-07", "2026-08", "2026-09", "2026-10", "2026-11"]  # nothing earlier
    assert t["basis"] == t["months"][:4]
    rows = {r["name"]: r for r in t["categories"]}
    assert "内部转账" not in rows
    assert (rows["房租"]["months"], rows["房租"]["average"], rows["房租"]["count"]) == (4, -50000, 5)
    assert rows["房租"]["values"]["2026-11"] == -50000  # shown, but not in the average
    assert (rows["饮食费"]["months"], rows["饮食费"]["average"]) == (3, -4000)  # months with transactions only
    assert rows["工资"]["values"] == {} and rows["工资"]["average"] is None  # no yen
    assert rows["交通费"]["values"] == {} and rows["交通费"]["months"] == 0  # on the list, no transactions yet
    assert t["currencies"] == ["日元", "人民币"]

    # All converted: a month without its rate can't be worked out
    for m in ["07", "08", "09", "10"]:
        client.put(f"/api/rates/2026-{m}/日元", json={"rate": 0.05})
    rows = {r["name"]: r for r in books.trend(today=today)["categories"]}
    assert rows["房租"]["values"]["2026-10"] == -2500 and rows["房租"]["values"]["2026-11"] is None
    assert rows["工资"]["average"] == 8000
    assert books.trend(today=today)["missing_rates"] == ["2026-11"]

    # A past window has no month in progress
    t = client.get("/api/trend?end=2026-09&span=3&currency=日元").json()
    assert t["months"] == ["2026-07", "2026-08", "2026-09"] and t["current"] is None and t["basis"] == t["months"]
    assert client.get("/api/trend?span=5").status_code == 400
    assert client.get("/api/trend?currency=没有").status_code == 404


def test_fixed_categories(client):
    from txreader import books
    from txreader.db import connect
    client.post("/api/settings/batch", json={"ops": [
        {"op": "add", "kind": "categories", "name": "房租"}, {"op": "add", "kind": "categories", "name": "家电"}]})
    cats = {c["name"]: c for c in client.get("/api/settings").json()["categories"]}
    assert not cats["房租"]["fixed"]  # new ones aren't
    ok = client.post("/api/settings/batch", json={"ops": [
        {"op": "set_fixed", "kind": "categories", "id": cats["房租"]["id"], "fixed": True}]})
    assert ok.status_code == 200
    cats = {c["name"]: c for c in client.get("/api/settings").json()["categories"]}
    assert cats["房租"]["fixed"] and not cats["家电"]["fixed"]
    assert {r["name"]: r["fixed"] for r in books.trend()["categories"]} == {"房租": True, "家电": False}
    # Only categories can be marked
    client.post("/api/settings/batch", json={"ops": [{"op": "add", "kind": "accounts", "name": "现金"}]})
    aid = client.get("/api/settings").json()["accounts"][0]["id"]
    assert client.post("/api/settings/batch", json={"ops": [
        {"op": "set_fixed", "kind": "accounts", "id": aid, "fixed": True}]}).status_code == 400

    # An older database gains the column, with the categories compared before marked
    from txreader import db
    with connect() as conn:
        conn.execute("ALTER TABLE categories DROP COLUMN fixed")
    db._initialized.clear()  # opened again as at start-up
    with connect() as conn:
        marked = {r[0]: r[1] for r in conn.execute("SELECT name, fixed FROM categories")}
    assert marked == {"房租": 1, "家电": 0}


def test_holdings(client):
    from datetime import date
    from txreader import portfolio
    assert client.get("/api/holdings").json()["accounts"] == []  # no investment account yet

    client.post("/api/transactions", json={"add_accounts": True, "rows": [
        {"date": "2026/10/05 10:00:00", "account": "证券", "currency": "日元", "amount": 50000,
         "category": "内部转账", "description": ""},
        {"date": "2026/10/20 10:00:00", "account": "证券", "currency": "日元", "amount": 10000,
         "category": "内部转账", "description": ""}]})  # after the week below
    ids = {a["name"]: a["id"] for a in client.get("/api/settings").json()["accounts"]}
    acc = ids["证券"]
    client.post("/api/settings/batch", json={"ops": [
        {"op": "add", "kind": "currencies", "name": "美元"}, {"op": "add", "kind": "currencies", "name": "人民币"},
        {"op": "add_currency", "id": acc, "name": "美元"}]})
    assert client.get(f"/api/holdings?account_id={acc}").status_code == 404  # not an investment account
    client.post("/api/settings/batch", json={"ops": [{"op": "set_type", "kind": "accounts", "id": acc, "type": "invest"}]})
    client.put("/api/opening", json={"start_month": "2026-09", "balances": [
        {"account_id": acc, "currency": "日元", "amount": 100000}]})
    client.put("/api/rates", json={"rates": [{"month": "2026-10", "currency": "日元", "rate": 0.05},
                                             {"month": "2026-10", "currency": "美元", "rate": 7}]})

    page = client.get("/api/holdings").json()
    assert page["accounts"] == [{"id": acc, "name": "证券", "currency": "日元", "currencies": ["日元", "美元"]}]
    assert page["account"] == acc and page["rows"] == [] and page["history"] == []

    week = "2026-10-12"  # Monday; the week ends on 10/18
    rows = [{"kind": "security", "name": "某某基金", "code": "0001", "currency": "日元", "quantity": 100, "price": 1200},
            {"kind": "cash", "currency": "日元", "amount": 20000},
            {"kind": "cash", "currency": "美元", "amount": 100}]
    assert client.put("/api/holdings", json={"account_id": acc, "week": "2026-10-05", "rows": [
        dict(rows[0], unit_cost=-1)]}).status_code == 400
    assert client.put("/api/holdings", json={"account_id": acc, "week": "2026-10-13", "rows": rows}).status_code == 400
    assert client.put("/api/holdings", json={"account_id": acc, "week": "2099-01-05", "rows": rows}).status_code == 400
    bad = [{"kind": "security", "name": "x", "currency": "日元", "quantity": "abc", "price": 1}]
    assert client.put("/api/holdings", json={"account_id": acc, "week": week, "rows": rows + bad}).status_code == 400
    assert client.put("/api/holdings", json={"account_id": acc, "week": week, "rows": rows}).is_success

    page = portfolio.holdings(acc, week, today=date(2026, 11, 12))
    assert page["filled"] and [r["kind"] for r in page["rows"]] == ["security", "cash", "cash"]
    v = page["valuation"]
    # 120,000 + 20,000 + 100 USD (x 7 / 0.05 = 14,000 yen); cost = 100,000 + 50,000
    assert [r["converted"] for r in v["rows"]] == [120000, 20000, 14000]
    assert (v["market_value"], v["cost"], v["gain"], v["ratio"]) == (154000, 150000, 4000, round(4000 / 150000, 6))
    assert page["history"] == [{"week": week, "market_value": 154000, "cost": 150000, "gain": 4000,
                                "ratio": round(4000 / 150000, 6), "position_gain": None, "position_ratio": None}]
    assert (v["position_cost"], v["uncosted"]) == (None, 1)  # no unit cost typed in

    # With unit costs: the position gain covers only the securities that have one
    costed = [dict(rows[0], unit_cost=1000), *rows[1:],
              {"kind": "security", "name": "某某股票", "currency": "美元", "quantity": 2, "price": 60, "unit_cost": 50},
              {"kind": "security", "name": "某某债券", "currency": "日元", "quantity": 1, "price": 500}]
    client.put("/api/holdings", json={"account_id": acc, "week": week, "rows": costed})
    v = portfolio.holdings(acc, week, today=date(2026, 11, 12))["valuation"]
    # 某某基金 120,000 vs 100,000; 某某股票 120 USD = 16,800 yen vs 100 USD = 14,000 yen
    assert [(r["cost_converted"], r["gain"]) for r in v["rows"]] == [
        (100000, 20000), (None, None), (None, None), (14000, 2800), (None, None)]
    assert v["rows"][0]["ratio"] == 0.2 and v["uncosted"] == 1
    # The same gains before converting: in each security's own currency, even without a rate
    assert [(r["own_gain"], r["own_ratio"]) for r in v["rows"]] == [
        (20000, 0.2), (None, None), (None, None), (20, 0.2), (None, None)]
    assert (v["position_cost"], v["position_gain"], v["position_ratio"]) == (114000, 22800, 0.2)
    assert v["market_value"] == 154000 + 16800 + 500
    client.put("/api/holdings", json={"account_id": acc, "week": week, "rows": rows})

    # A later week starts from the latest snapshot before it
    later = portfolio.holdings(acc, "2026-10-26", today=date(2026, 11, 12))
    assert not later["filled"] and later["copy_from"]["week"] == week and len(later["copy_from"]["rows"]) == 3
    assert later["valuation"]["market_value"] is None and later["valuation"]["cost"] == 160000

    # The balance sheet shows the latest valuation by the month's end
    bal = client.get("/api/balances?month=2026-10").json()
    assert bal["valuations"]["证券"] == {"account_id": acc, "week": week, "currency": "日元", "market_value": 154000,
                                        "ratio": round(4000 / 150000, 6), "position_ratio": None}
    assert client.get("/api/balances?month=2026-09").json()["valuations"] == {}

    # Valued in another currency: everything converted; a missing rate leaves it open
    client.put("/api/holdings", json={"account_id": acc, "week": week, "currency": "人民币", "rows": rows})
    v = portfolio.holdings(acc, week, today=date(2026, 11, 12))["valuation"]
    assert (v["currency"], v["market_value"], v["cost"]) == ("人民币", 7700, 7500)
    early = portfolio.holdings(acc, "2026-09-21", today=date(2026, 11, 12))["valuation"]
    assert early["cost"] is None and early["notes"]  # in the start month: no balance yet

    # In use: the account and its currencies can't be deleted
    assert client.post("/api/settings/batch", json={"ops": [{"op": "delete", "kind": "accounts", "id": acc}]}).status_code == 400
    # No rows: the snapshot is gone
    client.put("/api/holdings", json={"account_id": acc, "week": week, "rows": []})
    assert portfolio.holdings(acc, week, today=date(2026, 11, 12))["history"] == []


def test_holdings_prices(client, monkeypatch):
    """Prices from Yahoo Finance (faked): ended weeks get their close once, the latest snapshot is
    carried forward, this week's live prices are refreshed, typed-in prices are left alone."""
    from datetime import date
    from txreader import portfolio, quotes
    calls = []
    price = {"live": 12.5}

    def fake_closes(symbol, days):
        calls.append(("closes", symbol))
        return {"currency": "USD", "closes": {d: (float(d.day), d.isoformat()) for d in days}}

    def fake_latest(symbol):
        calls.append(("latest", symbol))
        return {"symbol": symbol, "name": "某某公司", "currency": "USD", "price": price["live"], "at": "2026-11-12T06:00+00:00"}
    monkeypatch.setattr(quotes, "closes", fake_closes)
    monkeypatch.setattr(quotes, "latest", fake_latest)
    today = date(2026, 11, 12)  # this week: 2026-11-09
    monkeypatch.setattr(portfolio, "_now", lambda: portfolio.datetime(2026, 11, 12, 6, 10, tzinfo=portfolio.timezone.utc))

    client.post("/api/transactions", json={"add_accounts": True, "rows": [
        {"date": "2026/10/05 10:00:00", "account": "证券", "currency": "美元", "amount": 1000,
         "category": "内部转账", "description": ""}]})
    acc = next(a["id"] for a in client.get("/api/settings").json()["accounts"] if a["name"] == "证券")
    client.post("/api/settings/batch", json={"ops": [{"op": "set_type", "kind": "accounts", "id": acc, "type": "invest"}]})
    rows = [{"kind": "security", "name": "某某公司", "code": "AAA", "symbol": "AAA", "price_source": "live",
             "currency": "美元", "quantity": 10, "price": 9},
            {"kind": "security", "name": "某某基金", "code": "0001", "currency": "美元", "quantity": 1, "price": 100,
             "symbol": "", "price_source": "live"},  # no symbol: always typed in
            {"kind": "cash", "currency": "美元", "amount": 50}]
    assert client.put("/api/holdings", json={"account_id": acc, "week": "2026-10-26", "rows": rows}).is_success
    assert [r["price_source"] for r in portfolio.holdings(acc, "2026-10-26", today=today)["rows"]] == ["live", "manual", "manual"]

    out = portfolio.sync(acc, today=today)
    assert out == {"changed": True, "warnings": []}
    got = lambda w: [(r["price"], r["price_source"], r["price_at"]) for r in portfolio.holdings(acc, w, today=today)["rows"]]
    # 10/26's week ended on 11/01 (faked close = the day of the month); 11/02 carried with its close;
    # this week carried with the live price; the fund keeps its typed-in price
    assert got("2026-10-26")[:2] == [(1.0, "close", "2026-11-01"), (100, "manual", None)]
    assert got("2026-11-02")[:2] == [(8.0, "close", "2026-11-08"), (100, "manual", None)]
    assert got("2026-11-09")[:2] == [(12.5, "live", "2026-11-12T06:00+00:00"), (100, "manual", None)]
    assert len(got("2026-11-09")) == 3

    # Opened again: nothing is fetched (closes are final, the live price is fresh)
    calls.clear()
    assert portfolio.sync(acc, today=today) == {"changed": False, "warnings": []} and calls == []
    # 「更新」 fetches the live price again
    price["live"] = 13
    assert client.post("/api/holdings/refresh", json={"account_id": acc}).status_code == 200  # (real date; nothing there)
    assert portfolio.refresh(acc, today=today) == {"updated": 1, "warnings": []}
    assert got("2026-11-09")[0][0] == 13
    # Next week: this week's live price becomes its close, and the carry goes on
    assert portfolio.sync(acc, today=date(2026, 11, 17))["changed"]
    assert got("2026-11-09")[0] == (15.0, "close", "2026-11-15")
    assert portfolio.holdings(acc, "2026-11-16", today=date(2026, 11, 17))["rows"][0]["price_source"] == "live"

    # Yahoo Finance down: the price stays as it was, still live, a warning says why
    def down(*a):
        raise quotes.QuoteError("连不上 Yahoo Finance")
    monkeypatch.setattr(quotes, "closes", down)
    out = portfolio.sync(acc, today=date(2026, 11, 24))
    assert out["warnings"] and portfolio.holdings(acc, "2026-11-16", today=date(2026, 11, 24))["rows"][0]["price_source"] == "live"

    # Lookup: the candidates with their price and the currency's name in the list
    monkeypatch.setattr(quotes, "search", lambda code: [{"symbol": "AAA", "name": "某某公司", "exchange": "NASDAQ", "type": "Equity"}])
    monkeypatch.setattr(quotes, "price_for", lambda s, w, t: {"price": 7, "currency": "USD", "source": "close", "at": "2026-11-06"})
    found = client.get("/api/holdings/lookup?code=AAA&week=2026-11-02").json()["candidates"]
    assert found == [{"symbol": "AAA", "name": "某某公司", "exchange": "NASDAQ", "type": "Equity", "price": 7,
                      "currency": "USD", "source": "close", "at": "2026-11-06", "currency_name": "美元"}]
    assert client.get("/api/holdings/lookup?code=%20&week=2026-11-02").status_code == 400
    # The search down (it often is): the code tried as a symbol; both down: 502
    monkeypatch.setattr(quotes, "search", down)
    monkeypatch.setattr(quotes, "latest", lambda s: {"symbol": s, "name": "某某公司", "currency": "USD", "price": 7, "at": None})
    assert [c["symbol"] for c in client.get("/api/holdings/lookup?code=aaa&week=2026-11-02").json()["candidates"]] == ["AAA"]
    assert quotes.guesses("7203") == ["7203", "7203.T", "7203.HK"] and quotes.guesses("600519")[1] == "600519.SS"
    monkeypatch.setattr(quotes, "price_for", down)
    assert client.get("/api/holdings/lookup?code=AAA&week=2026-11-02").status_code == 502


def test_currency_codes(client):
    """Codes are guessed from the name, can be changed, must be three letters and unique."""
    client.post("/api/transactions", json={"add_accounts": True, "rows": [
        {"date": "2026/10/05 10:00:00", "account": "现金", "currency": c, "amount": -1, "category": "饮食费", "description": ""}
        for c in ("人民币", "日元", "某某币")]})
    cur = {c["name"]: c for c in client.get("/api/settings").json()["currencies"]}
    assert {n: c["code"] for n, c in cur.items()} == {"人民币": "CNY", "日元": "JPY", "某某币": ""}
    assert client.post("/api/settings/currencies", json={"name": "usd"}).is_success
    assert {c["name"]: c["code"] for c in client.get("/api/settings").json()["currencies"]}["usd"] == "USD"
    op = lambda code: client.post("/api/settings/batch", json={"ops": [
        {"op": "set_code", "kind": "currencies", "id": cur["某某币"]["id"], "code": code}]})
    assert op("12").status_code == 400 and op("JPY").status_code == 400
    assert op("thb").is_success
    assert {c["name"]: c["code"] for c in client.get("/api/settings").json()["currencies"]}["某某币"] == "THB"


def test_fetched_rates(client, monkeypatch):
    """Rates from Yahoo Finance (faked): ended months get their month-end close once, this month
    the latest (fetched again when stale), typed-in rates are left alone, clearing one refetches it."""
    from datetime import date
    from txreader import fx, quotes
    calls = []
    live = {"rate": 0.0475}

    def fake_closes(symbol, days, digits=4):
        calls.append(("closes", symbol, sorted(days)))
        return {"currency": "CNY", "closes": {d: (d.day / 1000, d.isoformat()) for d in days}}

    def fake_latest(symbol, digits=4):
        calls.append(("latest", symbol))
        return {"symbol": symbol, "name": symbol, "currency": "CNY", "price": live["rate"], "at": "2026-11-12T06:00+00:00"}
    monkeypatch.setattr(quotes, "closes", fake_closes)
    monkeypatch.setattr(quotes, "latest", fake_latest)
    monkeypatch.setattr(fx, "_now", lambda: fx.datetime(2026, 11, 12, 6, 10, tzinfo=fx.timezone.utc))
    today = date(2026, 11, 12)

    client.post("/api/transactions", json={"add_accounts": True, "rows": [
        {"date": "2026/09/05 10:00:00", "account": "现金", "currency": c, "amount": -1, "category": "饮食费", "description": ""}
        for c in ("人民币", "日元", "某某币")]})  # 某某币 has no code: typed in
    client.put("/api/rates/2026-10/日元", json={"rate": 0.05})  # typed in: kept

    assert fx.sync(today) == {"changed": True, "updated": 2, "warnings": []}
    assert calls == [("closes", "JPYCNY=X", [date(2026, 9, 30)]), ("latest", "JPYCNY=X")]
    r = client.get("/api/rates").json()
    assert r["rates"] == {"2026-09": {"日元": 0.03}, "2026-10": {"日元": 0.05}, "2026-11": {"日元": 0.0475}}
    assert r["sources"] == {"2026-09": {"日元": {"source": "close", "at": "2026-09-30"}},
                            "2026-11": {"日元": {"source": "live", "at": "2026-11-12T06:00+00:00"}}}
    assert r["codes"] == {"日元": "JPY", "某某币": ""} and r["base_code"] == "CNY"

    # Opened again: nothing to fetch; 「更新汇率」 fetches this month's again
    calls.clear()
    assert fx.sync(today)["changed"] is False and calls == []
    live["rate"] = 0.048
    assert fx.refresh(today)["updated"] == 1 and calls == [("latest", "JPYCNY=X")]
    # Next month: November's live rate becomes its close; December is live
    calls.clear()
    assert fx.sync(date(2026, 12, 1))["updated"] == 2
    rates = client.get("/api/rates").json()["rates"]
    assert rates["2026-11"]["日元"] == 0.03 and rates["2026-12"]["日元"] == 0.048
    # Typing over a fetched rate makes it manual; clearing it lets the next sync fetch it
    client.put("/api/rates", json={"rates": [{"month": "2026-10", "currency": "日元", "rate": None},
                                             {"month": "2026-09", "currency": "日元", "rate": 0.04}]})
    calls.clear()
    fx.sync(date(2026, 12, 1))
    assert calls == [("closes", "JPYCNY=X", [date(2026, 10, 31)])]
    r = client.get("/api/rates").json()
    assert r["rates"]["2026-09"]["日元"] == 0.04 and "2026-09" not in r["sources"]

    # A currency whose rates were all fetched can be deleted (after its transactions are gone it would be)
    jpy = next(c for c in client.get("/api/settings").json()["currencies"] if c["name"] == "日元")
    assert jpy["blockers"] == ["有 1 笔交易", "手填了月汇率（先清空）"]
    # A new code drops the fetched rates
    client.post("/api/settings/batch", json={"ops": [{"op": "set_code", "kind": "currencies", "id": jpy["id"], "code": "USD"}]})
    assert set(client.get("/api/rates").json()["rates"]) == {"2026-09"}

    # Yahoo Finance down: nothing changes, a warning says why
    def down(*a, **k):
        raise quotes.QuoteError("连不上 Yahoo Finance")
    monkeypatch.setattr(quotes, "closes", down)
    out = fx.sync(date(2026, 12, 1))
    assert out["warnings"] and out["updated"] == 1  # the live rate still came
    # No code on the base currency: nothing fetched
    base = next(c for c in client.get("/api/settings").json()["currencies"] if c["name"] == "人民币")
    client.post("/api/settings/batch", json={"ops": [{"op": "set_code", "kind": "currencies", "id": base["id"], "code": ""}]})
    assert "本位币" in fx.sync(date(2026, 12, 1))["warnings"][0]


def test_account_in_a_pending_row_cant_be_deleted(client):
    acc = client.post("/api/settings/accounts", json={"name": "某银行"}).json()["id"]
    row = upload(client, b"shot1")["pending"][0]
    client.patch(f"/api/pending/{row['id']}", json={"account": "某银行"})
    r = client.delete(f"/api/settings/accounts/{acc}")
    assert r.status_code == 400 and "待核对" in r.json()["detail"]
    batch = client.post("/api/settings/batch", json={"ops": [{"op": "delete", "kind": "accounts", "id": acc}]})
    assert batch.status_code == 400
    client.post("/api/pending/delete", json={"ids": [row["id"]]})
    accounts = client.get("/api/settings").json()["accounts"]
    assert next(a for a in accounts if a["id"] == acc)["blockers"] == []
    assert client.delete(f"/api/settings/accounts/{acc}").is_success
