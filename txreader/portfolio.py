"""持仓: what each investment account holds, typed in once a week, and what it is worth.

- An investment account is one whose type is 投资 (lists.ACCOUNT_TYPES "invest").
- Each account is valued in one currency of its own (`accounts.value_currency_id`; by default
  the first of its currencies). A snapshot's rows are securities (quantity x price) and cash,
  each in any currency; they are converted into the account's currency at the monthly rates
  of the month the week ends in.
- Cost = the account's balance on the balance sheet at the end of that week (the opening balance
  plus the transactions since), converted the same way. Gain = market value - cost; the ratio is
  gain / cost. Nothing is booked: the balance sheet keeps showing the balances.
- Securities may also have a unit cost (成本价, what one unit cost on average). The position gain
  (持仓盈亏) = market value - quantity x unit cost, over the securities that have one, both
  converted at the week's rates. Unlike the gain against the balance, it leaves out cash, money
  moved in and out, and gains already realized.

- Prices (see quotes.py): a security with a Yahoo Finance symbol gets its price from there.
  During the week it is the latest one (price_source "live", refreshed by 「更新」 and when the
  page is opened); once the week has ended it becomes the close of the week's last trading day
  ("close") and is never fetched again. A price typed in ("manual") is never overwritten.
  `sync` does this when the page is opened, and carries the latest snapshot forward into the
  weeks since (quantities unchanged, prices fetched), so nothing has to be typed in each week.
  The symbols are fetched side by side (quotes.each).

Weeks run Monday to Sunday and are named by their Monday (2026-11-09).
"""
from datetime import date, datetime, timedelta, timezone

from . import books, quotes
from .db import connect, transaction
from .errors import NotFound

INVEST = "invest"
KINDS = {"security": "证券", "cash": "现金"}


class NoAccount(NotFound):
    detail = "没有这个投资账户"


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _check_week(week) -> date:
    try:
        d = date.fromisoformat(str(week))
    except ValueError:
        raise ValueError(f"周要写成那周周一的日期，比如 2026-11-09：{week}") from None
    if d.weekday() != 0:
        raise ValueError(f"{week} 不是周一")
    return d


def _week_end(week: str) -> str:
    """The Sunday, as transactions write dates (2026/11/15)."""
    return (date.fromisoformat(week) + timedelta(days=6)).strftime("%Y/%m/%d")


# ---------- accounts ----------

def _accounts(conn) -> list[dict]:
    """Investment accounts in list order: {"id", "name", "currency" (valued in), "currencies"}."""
    out = []
    for a in conn.execute("SELECT id, name, value_currency_id FROM accounts WHERE type = ? ORDER BY sort, id", (INVEST,)):
        own = [r[0] for r in conn.execute(
            "SELECT c.name FROM account_currencies ac JOIN currencies c ON c.id = ac.currency_id"
            " WHERE ac.account_id = ? ORDER BY c.sort, c.id", (a["id"],))]
        chosen = conn.execute("SELECT name FROM currencies WHERE id = ?", (a["value_currency_id"],)).fetchone()
        base = conn.execute("SELECT name FROM currencies WHERE is_base = 1").fetchone()
        currency = chosen[0] if chosen else own[0] if own else base[0] if base else None
        out.append({"id": a["id"], "name": a["name"], "currency": currency, "currencies": own})
    return out


def _account(conn, account_id: int) -> dict:
    for a in _accounts(conn):
        if a["id"] == account_id:
            return a
    raise NoAccount(account_id)


# ---------- snapshots ----------

def _rows(conn, account_id: int, week: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT p.kind, p.name, p.code, c.name AS currency, p.quantity, p.price, p.unit_cost, p.amount,"
        " p.symbol, p.price_source, p.price_at"
        " FROM positions p JOIN currencies c ON c.id = p.currency_id"
        " WHERE p.account_id = ? AND p.week = ? ORDER BY p.pos", (account_id, week))]


def _weeks(conn, account_id: int) -> list[str]:
    """Weeks with a snapshot, newest first."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT week FROM positions WHERE account_id = ? ORDER BY week DESC", (account_id,))]


def _row_value(r: dict) -> float:
    if r["kind"] == "cash":
        return r["amount"]
    return r["quantity"] * r["price"]


def _valuation(conn, account: dict, week: str, rows: list[dict]) -> dict:
    """{"currency", "rows": [{"value" (own currency), "converted" (account's currency or None),
                              "cost", "cost_converted", "gain", "ratio", "own_gain", "own_ratio"}:
                              the last six for securities with a unit cost, else None; own_* in
                              the security's own currency, the others in the account's],
        "market_value", "cost", "gain", "ratio": against the balance,
        "position_cost", "position_gain", "position_ratio": the securities with a unit cost,
        "uncosted": securities without one (each value None when it can't be worked out),
        "notes": [why not]}"""
    cur, end = account["currency"], _week_end(week)
    month = end[:7].replace("/", "-")
    rates = books._rates_for(conn, month)
    notes = []

    def convert(amount: float, frm: str):
        if not amount or frm == cur:
            return round(amount, 2)
        if frm in rates and cur in rates:
            return round(amount * rates[frm] / rates[cur], 2)
        missing = [c for c in (frm, cur) if c not in rates]
        note = f"{month} 缺{'、'.join(missing)}的汇率"
        if note not in notes:
            notes.append(note)
        return None

    values = []
    for r in rows:
        own = round(_row_value(r), 2)
        v = {"value": own, "converted": convert(own, r["currency"]),
             "cost": None, "cost_converted": None, "gain": None, "ratio": None,
             "own_gain": None, "own_ratio": None}
        if r["kind"] == "security" and r.get("unit_cost") is not None:
            v["cost"] = round(r["quantity"] * r["unit_cost"], 2)
            v["own_gain"] = round(own - v["cost"], 2)
            v["own_ratio"] = round(v["own_gain"] / v["cost"], 6) if v["cost"] > 0 else None
            v["cost_converted"] = convert(v["cost"], r["currency"])
            if v["converted"] is not None and v["cost_converted"] is not None:
                v["gain"] = round(v["converted"] - v["cost_converted"], 2)
                v["ratio"] = round(v["gain"] / v["cost_converted"], 6) if v["cost_converted"] > 0 else None
        values.append(v)
    market = books._sum(v["converted"] for v in values) if rows else None
    costed = [v for v in values if v["cost"] is not None]
    pos_cost = books._sum(v["cost_converted"] for v in costed) if costed else None
    pos_gain = books._sum(v["gain"] for v in costed) if costed and pos_cost is not None else None
    pos_ratio = round(pos_gain / pos_cost, 6) if pos_gain is not None and pos_cost > 0 else None
    uncosted = sum(1 for r in rows if r["kind"] == "security" and r.get("unit_cost") is None)

    cost = None
    start = books.start_month(conn)
    if not start:
        notes.append("还没设起始月，算不出结余（成本）")
    else:
        held = books._holdings_on(conn, start, end)
        if held is None:
            notes.append(f"这周在起始月（{start}）或之前，结余从起始月之后才有")
        else:
            parts = [convert(v, c) for (a, c), v in held.items() if a == account["name"]]
            cost = books._sum(parts) if parts else 0.0
    gain = round(market - cost, 2) if market is not None and cost is not None else None
    ratio = round(gain / cost, 6) if gain is not None and cost > 0 else None
    if gain is not None and cost <= 0:
        notes.append("成本（结余）不是正数，算不出盈亏比例")
    return {"currency": cur, "rows": values, "market_value": market, "cost": cost,
            "gain": gain, "ratio": ratio, "position_cost": pos_cost, "position_gain": pos_gain,
            "position_ratio": pos_ratio, "uncosted": uncosted, "notes": notes}


def holdings(account_id: int | None = None, week: str | None = None, today: date | None = None) -> dict:
    """The 持仓 page for one account and week:

    {"accounts": [investment accounts], "currencies": [all, list order], "account": id or None,
     "week", "this_week", "rows": [{"kind", "name", "code", "currency", "quantity", "price", "unit_cost", "amount",
                                    "symbol", "price_source", "price_at"}],
     "filled": whether this week has a snapshot, "copy_from": {"week", "rows"} the latest earlier
     snapshot (to start from when filling this week) or None, "valuation" (see _valuation),
     "history": [{"week", "market_value", "cost", "gain", "ratio", "position_gain", "position_ratio"}]
     every snapshot, newest first}"""
    this_week = monday(today or date.today()).isoformat()
    with connect() as conn:
        accounts = _accounts(conn)
        out = {"accounts": accounts, "this_week": this_week,
               "currencies": [r[0] for r in conn.execute("SELECT name FROM currencies ORDER BY sort, id")]}
        if not accounts and account_id is None:
            return out | {"account": None, "week": this_week, "rows": [], "filled": False, "copy_from": None,
                          "valuation": None, "history": []}
        account = _account(conn, account_id) if account_id is not None else accounts[0]
        week = _check_week(week).isoformat() if week else this_week
        if week > this_week:
            raise ValueError("不能填以后的周")
        rows = _rows(conn, account["id"], week)
        earlier = [w for w in _weeks(conn, account["id"]) if w < week]
        history = []
        for w in _weeks(conn, account["id"]):
            v = _valuation(conn, account, w, _rows(conn, account["id"], w))
            history.append({"week": w} | {k: v[k] for k in ("market_value", "cost", "gain", "ratio",
                                                             "position_gain", "position_ratio")})
        return out | {"account": account["id"], "week": week, "rows": rows, "filled": bool(rows),
                      "copy_from": {"week": earlier[0], "rows": _rows(conn, account["id"], earlier[0])}
                      if earlier and not rows else None,
                      "valuation": _valuation(conn, account, week, rows), "history": history}


def _num(v, what: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what}要是数字：{v}") from None


SOURCES = ("manual", "live", "close")


def save(account_id: int, week: str, rows: list[dict], currency: str | None = None,
         today: date | None = None) -> None:
    """Replace one week's snapshot (no rows: delete it), and set the account's currency if
    given. All or nothing. A security without a symbol is always priced by hand."""
    week = _check_week(week).isoformat()
    if week > monday(today or date.today()).isoformat():
        raise ValueError("不能填以后的周")
    with transaction() as conn:
        _account(conn, account_id)
        ids = {r[0]: r[1] for r in conn.execute("SELECT name, id FROM currencies")}
        if currency is not None:
            if currency not in ids:
                raise ValueError(f"没有币种「{currency}」")
            conn.execute("UPDATE accounts SET value_currency_id = ? WHERE id = ?", (ids[currency], account_id))
        clean = []
        for i, r in enumerate(rows, 1):
            kind = r.get("kind")
            if kind not in KINDS:
                raise ValueError(f"第 {i} 行：不认识的类型 {kind}")
            cur = str(r.get("currency") or "").strip()
            if cur not in ids:
                raise ValueError(f"第 {i} 行：没有币种「{cur}」")
            name, code = str(r.get("name") or "").strip(), str(r.get("code") or "").strip()
            if kind == "security":
                if not name and not code:
                    raise ValueError(f"第 {i} 行：证券要填名称或代码")
                qty = _num(r.get("quantity"), f"第 {i} 行的数量")
                price = _num(r.get("price"), f"第 {i} 行的现价")
                if price < 0:
                    raise ValueError(f"第 {i} 行：现价不能是负数")
                unit_cost = r.get("unit_cost")
                unit_cost = None if unit_cost in (None, "") else _num(unit_cost, f"第 {i} 行的成本价")
                if unit_cost is not None and unit_cost < 0:
                    raise ValueError(f"第 {i} 行：成本价不能是负数")
                symbol = str(r.get("symbol") or "").strip()
                source = r.get("price_source") or "manual"
                if source not in SOURCES:
                    raise ValueError(f"第 {i} 行：不认识的价格来源 {source}")
                if not symbol:
                    source = "manual"
                at = (r.get("price_at") or None) if source != "manual" else None
                clean.append((kind, name, code, ids[cur], qty, price, unit_cost, None, symbol, source, at))
            else:
                clean.append((kind, name, code, ids[cur], None, None, None, _num(r.get("amount"), f"第 {i} 行的金额"),
                              "", "manual", None))
        conn.execute("DELETE FROM positions WHERE account_id = ? AND week = ?", (account_id, week))
        _insert(conn, account_id, week, clean)


def _insert(conn, account_id: int, week: str, rows: list[tuple]) -> None:
    """rows: (kind, name, code, currency_id, quantity, price, unit_cost, amount, symbol, price_source, price_at)"""
    conn.executemany(
        "INSERT INTO positions (account_id, week, pos, kind, name, code, currency_id, quantity, price, unit_cost,"
        " amount, symbol, price_source, price_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(account_id, week, i, *row) for i, row in enumerate(rows, 1)])


# ---------- prices ----------

STALE = timedelta(minutes=30)  # a live price older than this is fetched again when the page is opened


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sunday(week: str) -> date:
    return date.fromisoformat(week) + timedelta(days=6)


def _fetch_closes(symbols: set[str], weeks: set[str], warnings: list[str]) -> dict:
    """(symbol, week) -> (price, trading day) for the weeks' Sundays; failures go to warnings."""
    out = {}
    sundays = [_sunday(w) for w in weeks]
    for s, got in quotes.each(lambda s: quotes.closes(s, sundays)["closes"], sorted(symbols)):
        if isinstance(got, quotes.QuoteError):
            warnings.append(f"{s}：{got}")
            continue
        for w in weeks:
            if _sunday(w) in got:
                out[s, w] = got[_sunday(w)]
        if not any((s, w) in out for w in weeks):
            warnings.append(f"{s}：Yahoo Finance 没有这几周的收盘价")
    return out


def _fetch_latest(symbols: set[str], warnings: list[str]) -> dict:
    """symbol -> (price, at)."""
    out = {}
    for s, q in quotes.each(lambda s: quotes.latest(s), sorted(symbols)):
        if isinstance(q, quotes.QuoteError):
            warnings.append(f"{s}：{q}")
            continue
        if q:
            out[s] = (q["price"], q["at"] or _now().isoformat(timespec="minutes"))
        else:
            warnings.append(f"{s}：Yahoo Finance 没有现价")
    return out


def refresh(account_id: int, today: date | None = None) -> dict:
    """「更新」: fetch this week's live prices again. {"updated": n, "warnings": [...]}"""
    this_week = monday(today or date.today()).isoformat()
    with connect() as conn:
        _account(conn, account_id)
        symbols = {r[0] for r in conn.execute(
            "SELECT symbol FROM positions WHERE account_id = ? AND week = ? AND symbol != '' AND price_source = 'live'",
            (account_id, this_week))}
    warnings = []
    got = _fetch_latest(symbols, warnings)
    with transaction() as conn:
        for s, (price, at) in got.items():
            conn.execute("UPDATE positions SET price = ?, price_at = ? WHERE account_id = ? AND week = ?"
                         " AND symbol = ? AND price_source = 'live'", (price, at, account_id, this_week, s))
    return {"updated": len(got), "warnings": warnings}


def sync(account_id: int, today: date | None = None) -> dict:
    """When the page is opened: (1) the ended weeks' live prices become the week's close (final);
    (2) the latest snapshot is carried forward into each week since, up to this one (rows with a
    symbol get that week's price; typed-in prices are copied); (3) this week's live prices older
    than STALE are fetched again. {"changed": bool, "warnings": [...]}. A price that can't be had
    stays as it was (still "live") and is tried again next time."""
    this_week = monday(today or date.today()).isoformat()
    with connect() as conn:
        _account(conn, account_id)
        weeks = _weeks(conn, account_id)
        pending = [dict(r) for r in conn.execute(
            "SELECT week, pos, symbol, price_at FROM positions WHERE account_id = ? AND kind = 'security'"
            " AND symbol != '' AND price_source = 'live'", (account_id,))]
        carry_from = next((w for w in weeks if w < this_week), None) if this_week not in weeks else None
        base = _rows(conn, account_id, carry_from) if carry_from else []
        cur_ids = {r[0]: r[1] for r in conn.execute("SELECT name, id FROM currencies")}
    new_weeks = []
    if carry_from:
        w = date.fromisoformat(carry_from) + timedelta(days=7)
        while w.isoformat() <= this_week:
            new_weeks.append(w.isoformat())
            w += timedelta(days=7)
    carried_symbols = {r["symbol"] for r in base if r["kind"] == "security" and r["symbol"]}

    warnings = []
    ended = {p["week"] for p in pending if p["week"] < this_week} | {w for w in new_weeks if w < this_week}
    close_symbols = {p["symbol"] for p in pending if p["week"] < this_week} | (carried_symbols if ended else set())
    closes = _fetch_closes(close_symbols, ended, warnings) if close_symbols else {}
    cutoff = (_now() - STALE).isoformat(timespec="minutes")
    live_symbols = {p["symbol"] for p in pending if p["week"] == this_week and (p["price_at"] or "") < cutoff}
    if this_week in new_weeks:
        live_symbols |= carried_symbols
    live = _fetch_latest(live_symbols, warnings) if live_symbols else {}

    changed = False
    with transaction() as conn:
        for p in pending:
            if p["week"] < this_week and (p["symbol"], p["week"]) in closes:
                price, day = closes[p["symbol"], p["week"]]
                conn.execute("UPDATE positions SET price = ?, price_source = 'close', price_at = ?"
                             " WHERE account_id = ? AND week = ? AND pos = ?", (price, day, account_id, p["week"], p["pos"]))
                changed = True
            elif p["week"] == this_week and p["symbol"] in live:
                price, at = live[p["symbol"]]
                conn.execute("UPDATE positions SET price = ?, price_at = ? WHERE account_id = ? AND week = ? AND pos = ?",
                             (price, at, account_id, p["week"], p["pos"]))
                changed = True
        for w in new_weeks:
            if conn.execute("SELECT 1 FROM positions WHERE account_id = ? AND week = ?", (account_id, w)).fetchone():
                continue  # saved meanwhile
            rows = []
            for r in base:
                price, source, at = r["price"], r["price_source"], r["price_at"]
                if r["kind"] == "security" and r["symbol"]:
                    got = closes.get((r["symbol"], w)) if w < this_week else live.get(r["symbol"])
                    # not had: the last price, still live so it is tried again
                    price, source, at = (got[0], "close" if w < this_week else "live", got[1]) if got else (price, "live", None)
                rows.append((r["kind"], r["name"], r["code"], cur_ids[r["currency"]], r["quantity"], price,
                             r["unit_cost"], r["amount"], r["symbol"], source, at))
            _insert(conn, account_id, w, rows)
            changed = True
    return {"changed": changed, "warnings": warnings}


def lookup(code: str, week: str, today: date | None = None) -> list[dict]:
    """Candidates for a code, priced for the week (see quotes.lookup)."""
    week = _check_week(week).isoformat()
    with connect() as conn:
        names = {r[0]: r[1] for r in conn.execute("SELECT code, name FROM currencies WHERE code != ''")}
    return quotes.lookup(code.strip(), week, monday(today or date.today()).isoformat(), names)


def at_month_end(month: str) -> dict[str, dict]:
    """For the balance sheet: account name -> the latest snapshot in a week ending by the end of
    `month`: {"account_id", "week", "currency", "market_value", "ratio" (against the balance), "position_ratio"}."""
    y, m = int(month[:4]), int(month[5:])
    last = (date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1))
    with connect() as conn:
        out = {}
        for a in _accounts(conn):
            w = conn.execute("SELECT MAX(week) FROM positions WHERE account_id = ? AND week <= ?",
                             (a["id"], (last - timedelta(days=6)).isoformat())).fetchone()[0]
            if w:
                v = _valuation(conn, a, w, _rows(conn, a["id"], w))
                out[a["name"]] = {"account_id": a["id"], "week": w, "currency": a["currency"], "market_value": v["market_value"],
                                  "ratio": v["ratio"], "position_ratio": v["position_ratio"]}
        return out
