"""持仓: what each investment account holds, day by day, and what it is worth.

- An investment account is one whose type is 投资 (lists.ACCOUNT_TYPES "invest").
- Holdings are typed in only when they change: a snapshot (positions.day) is the whole table on
  that day and holds until the next one. A day's holdings = the latest snapshot on or before it.
- Each account is valued in one currency of its own (`accounts.value_currency_id`; by default
  the first of its currencies). A snapshot's rows are securities (quantity x price) and cash,
  each in any currency; they are converted into the account's currency at the day's rates.
- Prices: a security with a Yahoo Finance symbol is priced from the daily closes in `prices`:
  on a day, the close of the last trading day on or before it (weekends and holidays keep the
  Friday's); the newest bar is the latest price. Before its first bar, or without a symbol, the
  price saved with the snapshot is used, so a typed-in price holds until the next snapshot.
- Rates: also daily, from the same table (JPYCNY=X, see quotes.fx_symbol) for currencies with a
  code; otherwise, or before the first bar, the month's rate (fx_rates). The month's rate is the
  close of its last trading day, so on a month's last day the two agree (unless typed in).
- Cost = the account's balance on the balance sheet at the end of that day (the opening balance
  plus the transactions since), converted the same way. Gain = market value - cost; the ratio is
  gain / cost. Nothing is booked: the balance sheet keeps showing the balances.
- Securities may also have a unit cost (成本价, what one unit cost on average). The position gain
  (持仓盈亏) = market value - quantity x unit cost, over the securities that have one, both
  converted at the day's rates. Unlike the gain against the balance, it leaves out cash, money
  moved in and out, and gains already realized.
- Fetching (`sync`, when the page is opened; `refresh`, 「更新价格」): each symbol the account
  needs, and each of its currencies' rates, in one call from the first day needed to now
  (quotes.daily), side by side (quotes.each). Again only after STALE, and then from a week before
  the last fetch, so a bar fetched during a session is replaced by its close. A symbol no longer
  held is not fetched again once its last day is two days past.
"""
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone

from . import books, quotes
from .db import connect, transaction
from .errors import NotFound

INVEST = "invest"
KINDS = {"security": "证券", "cash": "现金"}
STALE = timedelta(minutes=30)  # prices fetched longer ago than this are fetched again when the page is opened


class NoAccount(NotFound):
    detail = "没有这个投资账户"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _check_day(day, today: date) -> str:
    try:
        d = date.fromisoformat(str(day))
    except ValueError:
        raise ValueError(f"日期要写成 2026-11-14 这样：{day}") from None
    if d > today:
        raise ValueError("不能填以后的日子")
    return d.isoformat()


def _last_day(month: str) -> date:
    y, m = int(month[:4]), int(month[5:])
    return date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)


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


# ---------- valuing ----------

class _Book:
    """One account's snapshots, prices, rates and balances, read once to value any day."""

    def __init__(self, conn, account: dict):
        self.account = account
        self.snaps: dict[str, list[dict]] = {}  # day -> rows as saved
        for r in conn.execute(
                "SELECT p.day, p.kind, p.name, p.code, c.name AS currency, p.quantity, p.price, p.unit_cost, p.amount, p.symbol"
                " FROM positions p JOIN currencies c ON c.id = p.currency_id WHERE p.account_id = ? ORDER BY p.day, p.pos",
                (account["id"],)):
            r = dict(r)
            self.snaps.setdefault(r.pop("day"), []).append(r)
        self.days = sorted(self.snaps)

        base = conn.execute("SELECT name, code FROM currencies WHERE is_base = 1").fetchone()
        self.base = base["name"] if base else None
        self.fx = {r[0]: quotes.fx_symbol(r[1], base["code"]) for r in conn.execute("SELECT name, code FROM currencies")
                   if base and base["code"] and r[1] and r[0] != base["name"]}  # currency name -> rate symbol
        self.monthly = {(r[0], r[1]): r[2] for r in conn.execute(
            "SELECT f.month, c.name, f.rate FROM fx_rates f JOIN currencies c ON c.id = f.currency_id")}
        symbols = sorted({r["symbol"] for rows in self.snaps.values() for r in rows if r["symbol"]} | set(self.fx.values()))
        self.bars: dict[str, tuple[list[str], list[tuple]]] = {}  # symbol -> ([days], [(close, day, at)])
        if symbols:
            for r in conn.execute(f"SELECT symbol, day, close, at FROM prices WHERE symbol IN ({', '.join('?' * len(symbols))})"
                                  " ORDER BY symbol, day", symbols):
                days, bars = self.bars.setdefault(r[0], ([], []))
                days.append(r[1])
                bars.append((r[2], r[1], r[3]))

        # Balances: the opening balance plus the transactions after the start month, by day
        self.start = books.start_month(conn)
        self.opening, self.tx_days, self.tx_sums = {}, [], []
        if self.start:
            self.opening = {c: v for (a, c), v in books._holdings(conn, self.start, self.start).items() if a == account["name"]}
            running: dict[str, float] = {}
            for d, cur, amount in conn.execute(
                    "SELECT substr(date, 1, 10), currency, SUM(amount) FROM transactions"
                    " WHERE account = ? AND substr(date, 1, 7) > ? GROUP BY 1, 2 ORDER BY 1",
                    (account["name"], self.start.replace("-", "/"))):
                running[cur] = round(running.get(cur, 0) + amount, 2)
                d = d.replace("/", "-")
                if self.tx_days and self.tx_days[-1] == d:
                    self.tx_sums[-1] = dict(running)
                else:
                    self.tx_days.append(d)
                    self.tx_sums.append(dict(running))

    def snapshot_on(self, day: str) -> str | None:
        i = bisect_right(self.days, day)
        return self.days[i - 1] if i else None

    def price(self, symbol: str, day: str) -> tuple | None:
        """(close, trading day, at) of the last bar on or before the day."""
        days, bars = self.bars.get(symbol, ([], []))
        i = bisect_right(days, day)
        return bars[i - 1] if i else None

    def rate(self, currency: str, day: str) -> float | None:
        if currency == self.base:
            return 1.0
        bar = self.price(self.fx[currency], day) if currency in self.fx else None
        return bar[0] if bar else self.monthly.get((day[:7], currency))

    def balance(self, day: str) -> dict[str, float] | None:
        """currency -> the balance at the end of the day; None when the day isn't after the start
        month (only the start month's end is known before that)."""
        if not self.start or day[:7] <= self.start:
            return None
        i = bisect_right(self.tx_days, day)
        out = dict(self.opening)
        for c, v in (self.tx_sums[i - 1] if i else {}).items():
            out[c] = round(out.get(c, 0) + v, 2)
        return out

    def priced(self, day: str) -> list[dict]:
        """The day's rows, securities with the day's price: {..., "price_source": "close" / "live"
        (the newest bar) / "saved" (a symbol without a bar yet) / "manual" (typed in), "price_at"}."""
        snap = self.snapshot_on(day)
        out = []
        for r in self.snaps[snap] if snap else []:
            r = dict(r, price_source="manual", price_at=None)
            if r["kind"] == "security" and r["symbol"]:
                bar = self.price(r["symbol"], day)
                r |= ({"price": bar[0], "price_source": "live" if bar[2] else "close", "price_at": bar[2] or bar[1]}
                      if bar else {"price_source": "saved"})
            out.append(r)
        return out

    def valuation(self, day: str, rows: list[dict] | None = None) -> dict:
        """{"currency", "rows": [{"value" (own currency), "converted" (account's currency or None),
                                  "cost", "cost_converted", "gain", "ratio", "own_gain", "own_ratio"}:
                                  the last six for securities with a unit cost, else None; own_* in
                                  the security's own currency, the others in the account's],
            "market_value", "cost", "gain", "ratio": against the balance,
            "position_cost", "position_gain", "position_ratio": the securities with a unit cost,
            "uncosted": securities without one (each value None when it can't be worked out),
            "notes": [why not]}"""
        rows = self.priced(day) if rows is None else rows
        cur = self.account["currency"]
        notes = []

        def convert(amount: float, frm: str):
            if not amount or frm == cur:
                return round(amount, 2)
            a, b = self.rate(frm, day), self.rate(cur, day)
            if a is not None and b is not None:
                return round(amount * a / b, 2)
            missing = [c for c, r in ((frm, a), (cur, b)) if r is None]
            note = f"{day} 缺{'、'.join(missing)}的汇率"
            if note not in notes:
                notes.append(note)
            return None

        values = []
        for r in rows:
            own = round(r["amount"] if r["kind"] == "cash" else r["quantity"] * r["price"], 2)
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
        held = self.balance(day)
        if not self.start:
            notes.append("还没设起始月，算不出结余（成本）")
        elif held is None:
            notes.append(f"这天在起始月（{self.start}）或之前，结余从起始月之后才有")
        else:
            cost = books._sum(convert(v, c) for c, v in held.items())
        gain = round(market - cost, 2) if market is not None and cost is not None else None
        ratio = round(gain / cost, 6) if gain is not None and cost > 0 else None
        if gain is not None and cost <= 0:
            notes.append("成本（结余）不是正数，算不出盈亏比例")
        return {"currency": cur, "rows": values, "market_value": market, "cost": cost,
                "gain": gain, "ratio": ratio, "position_cost": pos_cost, "position_gain": pos_gain,
                "position_ratio": pos_ratio, "uncosted": uncosted, "notes": notes}

    def series(self, today: str) -> list[dict]:
        """Every day from the first snapshot to today, oldest first: {"day", "market_value", "cost",
        "gain", "ratio", "position_gain", "position_ratio", "change" (a snapshot that day)}."""
        out = []
        if not self.days:
            return out
        d, end = date.fromisoformat(self.days[0]), date.fromisoformat(today)
        while d <= end:
            day = d.isoformat()
            v = self.valuation(day)
            out.append({"day": day} | {k: v[k] for k in ("market_value", "cost", "gain", "ratio",
                                                          "position_gain", "position_ratio")} | {"change": day in self.snaps})
            d += timedelta(days=1)
        return out


# ---------- the page ----------

def holdings(account_id: int | None = None, day: str | None = None, today: date | None = None) -> dict:
    """The 持仓 page for one account and day:

    {"accounts": [investment accounts], "currencies": [all, list order], "account": id or None,
     "day", "today", "snapshot": the day of the snapshot in effect (None: nothing yet),
     "rows": [{"kind", "name", "code", "currency", "quantity", "price" (the day's), "unit_cost", "amount",
               "symbol", "price_source", "price_at"}] (see _Book.priced), "valuation" (see _Book.valuation),
     "changes": the days with a snapshot, newest first, "series": every day's values (see _Book.series)}"""
    today = today or date.today()
    with connect() as conn:
        accounts = _accounts(conn)
        out = {"accounts": accounts, "today": today.isoformat(),
               "currencies": [r[0] for r in conn.execute("SELECT name FROM currencies ORDER BY sort, id")]}
        if not accounts and account_id is None:
            return out | {"account": None, "day": today.isoformat(), "snapshot": None, "rows": [],
                          "valuation": None, "changes": [], "series": []}
        account = _account(conn, account_id) if account_id is not None else accounts[0]
        day = _check_day(day, today) if day else today.isoformat()
        book = _Book(conn, account)
    rows = book.priced(day)
    return out | {"account": account["id"], "day": day, "snapshot": book.snapshot_on(day), "rows": rows,
                  "valuation": book.valuation(day, rows), "changes": book.days[::-1], "series": book.series(today.isoformat())}


def at_month_end(month: str, today: date | None = None) -> dict[str, dict]:
    """For the balance sheet: account name -> its value at the end of `month` (today for this
    month): {"account_id", "day", "currency", "market_value", "ratio" (against the balance),
    "position_ratio"}; accounts with nothing typed in by then are left out."""
    today = today or date.today()
    day = min(_last_day(month), today).isoformat()
    out = {}
    with connect() as conn:
        for a in _accounts(conn):
            book = _Book(conn, a)
            if book.snapshot_on(day):
                v = book.valuation(day)
                out[a["name"]] = {"account_id": a["id"], "day": day, "currency": a["currency"],
                                  "market_value": v["market_value"], "ratio": v["ratio"], "position_ratio": v["position_ratio"]}
    return out


# ---------- saving ----------

def _num(v, what: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what}要是数字：{v}") from None


def save(account_id: int, day: str, rows: list[dict] | None, currency: str | None = None,
         today: date | None = None) -> None:
    """Set the snapshot from `day` on (replacing that day's, if any), and the account's currency
    if given; rows None: only the currency. All or nothing. A security with a symbol is priced from
    Yahoo Finance; its price here is kept for the days before the first one fetched."""
    day = _check_day(day, today or date.today())
    with transaction() as conn:
        _account(conn, account_id)
        ids = {r[0]: r[1] for r in conn.execute("SELECT name, id FROM currencies")}
        if currency is not None:
            if currency not in ids:
                raise ValueError(f"没有币种「{currency}」")
            conn.execute("UPDATE accounts SET value_currency_id = ? WHERE id = ?", (ids[currency], account_id))
        if rows is None:
            return
        if not rows:
            raise ValueError("持仓至少要有一行；全卖掉了可以留一行金额为 0 的现金。要去掉这天的记录请用「删除」")
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
                clean.append((kind, name, code, ids[cur], qty, price, unit_cost, None, str(r.get("symbol") or "").strip()))
            else:
                clean.append((kind, name, code, ids[cur], None, None, None, _num(r.get("amount"), f"第 {i} 行的金额"), ""))
        conn.execute("DELETE FROM positions WHERE account_id = ? AND day = ?", (account_id, day))
        conn.executemany(
            "INSERT INTO positions (account_id, day, pos, kind, name, code, currency_id, quantity, price, unit_cost,"
            " amount, symbol) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(account_id, day, i, *row) for i, row in enumerate(clean, 1)])


def delete(account_id: int, day: str) -> None:
    """Remove one day's snapshot: the one before it holds on until the next."""
    with transaction() as conn:
        _account(conn, account_id)
        if not conn.execute("DELETE FROM positions WHERE account_id = ? AND day = ?", (account_id, str(day))).rowcount:
            raise ValueError(f"{day} 没有持仓记录")


# ---------- prices ----------

def _needs(conn, account: dict, today: date) -> dict[str, tuple[str, str, bool]]:
    """symbol -> (first day, last day needed, is a rate) for the account."""
    book = _Book(conn, account)
    if not book.days:
        return {}
    t = today.isoformat()
    out: dict[str, list] = {}
    for i, day in enumerate(book.days):
        until = (date.fromisoformat(book.days[i + 1]) - timedelta(days=1)).isoformat() if i + 1 < len(book.days) else t
        for r in book.snaps[day]:
            if r["symbol"]:
                got = out.setdefault(r["symbol"], [day, until, False])
                got[1] = max(got[1], until)
    held = {r["currency"] for rows in book.snaps.values() for r in rows} | {account["currency"]}
    for c in held:
        if c in book.fx:
            out[book.fx[c]] = [book.days[0], t, True]
    return {s: tuple(v) for s, v in out.items()}


def sync(account_id: int, today: date | None = None, force: bool = False) -> dict:
    """Fetch the daily closes the account needs (see the module's docstring); `force`: whatever
    is still needed, now. {"changed": bool, "updated": symbols fetched, "warnings": [...]}. What
    can't be had stays as it was and is tried again next time."""
    today = today or date.today()
    now = _now()
    cutoff = (now - STALE).isoformat(timespec="seconds")
    with connect() as conn:
        account = _account(conn, account_id)
        needs = _needs(conn, account, today)
        fetched = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT symbol, start, fetched_at FROM price_fetches")}
        base = conn.execute("SELECT code FROM currencies WHERE is_base = 1").fetchone()
    jobs = []  # (symbol, from day, is a rate)
    for s, (first, last, is_rate) in sorted(needs.items()):
        had = fetched.get(s)
        covered = had is not None and had[0] <= first
        if covered and had[1][:10] >= (date.fromisoformat(last) + timedelta(days=2)).isoformat():
            continue  # its last day's close is final
        if covered and not force and had[1] >= cutoff:
            continue
        frm = first if not covered else max(first, (date.fromisoformat(had[1][:10]) - timedelta(days=7)).isoformat())
        jobs.append((s, frm, is_rate))

    warnings, got = [], {}
    results = quotes.each(lambda j: quotes.daily(j[0], date.fromisoformat(j[1]), digits=None if j[2] else 4), jobs)
    for (s, frm, is_rate), res in results:
        if isinstance(res, quotes.QuoteError):
            warnings.append(f"{s}：{res}")
        elif not res or not res["bars"]:
            warnings.append(f"{s}：Yahoo Finance 没有这个代码的价格")
        elif is_rate and base and res["currency"] and res["currency"] != base["code"]:
            warnings.append(f"{s}：Yahoo Finance 给的是 {res['currency']}，不是 {base['code']}")
        else:
            got[s] = (frm, res)
    stamp = now.isoformat(timespec="seconds")
    with transaction() as conn:
        for s, (frm, res) in got.items():
            last = res["bars"][-1][0]
            conn.executemany(
                "INSERT INTO prices (symbol, day, close, at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (symbol, day) DO UPDATE SET close = excluded.close, at = excluded.at",
                [(s, d, c, res["at"] if d == last else None) for d, c in res["bars"]])
            conn.execute("UPDATE prices SET at = NULL WHERE symbol = ? AND day < ? AND at IS NOT NULL", (s, last))
            conn.execute("INSERT INTO price_fetches (symbol, start, fetched_at) VALUES (?, ?, ?)"
                         " ON CONFLICT (symbol) DO UPDATE SET start = min(start, excluded.start), fetched_at = excluded.fetched_at",
                         (s, frm, stamp))
    return {"changed": bool(got), "updated": len(got), "warnings": warnings}


def refresh(account_id: int, today: date | None = None) -> dict:
    """「更新价格」: fetch again now. {"updated": n, "warnings": [...]}"""
    out = sync(account_id, today, force=True)
    return {"updated": out["updated"], "warnings": out["warnings"]}


def lookup(code: str, day: str, today: date | None = None) -> list[dict]:
    """Candidates for a code, priced on the day (see quotes.lookup)."""
    today = today or date.today()
    day = _check_day(day, today)
    with connect() as conn:
        names = {r[0]: r[1] for r in conn.execute("SELECT code, name FROM currencies WHERE code != ''")}
    return quotes.lookup(code.strip(), day, today.isoformat(), names)
