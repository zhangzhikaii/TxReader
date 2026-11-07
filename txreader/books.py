"""Opening balances and monthly exchange rates: what the balance sheet and the monthly report
(docs/plan.md, steps 4 and 5) are computed from, together with the transactions.

- Opening balances: what each account held in each currency at the end of the start month.
  Later months follow from them plus the transactions after the start month.
- Monthly rates: typed in by hand, 1 unit of a currency = rate units of the base currency.
  The base currency itself is always 1 and not stored.
"""
import re
from datetime import date

from .db import connect

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class NotFound(LookupError):
    pass


def _check_month(month) -> str:
    if not isinstance(month, str) or not MONTH_RE.match(month):
        raise ValueError(f"月份格式不对：{month}，应为 2026-10")
    return month


def _currency_id(conn, name: str) -> int:
    r = conn.execute("SELECT id FROM currencies WHERE name = ?", (name,)).fetchone()
    if not r:
        raise NotFound(name)
    return r[0]


def start_month(conn) -> str | None:
    r = conn.execute("SELECT value FROM config WHERE key = 'start_month'").fetchone()
    return r[0] if r else None


def set_start_month(month: str | None) -> None:
    with connect() as conn:
        if month:
            conn.execute("INSERT INTO config (key, value) VALUES ('start_month', ?)"
                         " ON CONFLICT (key) DO UPDATE SET value = excluded.value", (_check_month(month),))
        else:
            conn.execute("DELETE FROM config WHERE key = 'start_month'")


# ---------- opening balances ----------

def opening() -> dict:
    """{"start_month", "currencies": [names in list order],
        "accounts": [{"id", "name", "balances": {currency: amount or None}}]}
    Only the currencies each account uses are listed for it."""
    with connect() as conn:
        currencies = [r[0] for r in conn.execute("SELECT name FROM currencies ORDER BY sort, id")]
        accounts = [{"id": r["id"], "name": r["name"], "balances": {}} for r in conn.execute(
            "SELECT id, name FROM accounts ORDER BY sort, id")]
        by_id = {a["id"]: a for a in accounts}
        for r in conn.execute(
                "SELECT ac.account_id, c.name, ob.amount FROM account_currencies ac"
                " JOIN currencies c ON c.id = ac.currency_id"
                " LEFT JOIN opening_balances ob ON ob.account_id = ac.account_id AND ob.currency_id = ac.currency_id"
                " ORDER BY c.sort, c.id"):
            by_id[r["account_id"]]["balances"][r["name"]] = r["amount"]
        return {"start_month": start_month(conn), "currencies": currencies, "accounts": accounts}


def set_opening(account_id: int, currency: str, amount: float | None) -> None:
    """amount None clears it."""
    with connect() as conn:
        cid = _currency_id(conn, currency)
        if not conn.execute("SELECT 1 FROM account_currencies WHERE account_id = ? AND currency_id = ?",
                            (account_id, cid)).fetchone():
            raise ValueError(f"这个账户还没有币种「{currency}」，先在账户清单里加上")
        if amount is None:
            conn.execute("DELETE FROM opening_balances WHERE account_id = ? AND currency_id = ?", (account_id, cid))
        else:
            conn.execute("INSERT INTO opening_balances (account_id, currency_id, amount) VALUES (?, ?, ?)"
                         " ON CONFLICT (account_id, currency_id) DO UPDATE SET amount = excluded.amount",
                         (account_id, cid, round(float(amount), 2)))


# ---------- monthly rates ----------

def _shift(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    y, m = divmod(y * 12 + m - 1 + n, 12)
    return f"{y}-{m + 1:02d}"


def months_between(first: str, last: str) -> list[str]:
    out, m = [], first
    while m <= last:
        out.append(m)
        m = _shift(m, 1)
    return out


def rates(today: date | None = None) -> dict:
    """{"base": name or None, "currencies": [the others, in list order],
        "months": [newest first], "rates": {month: {currency: rate}}}
    Months run from the start month (or the first transaction, if earlier) to this month (or
    the last transaction, if later), plus any month that already has a rate."""
    today = today or date.today()
    with connect() as conn:
        base = conn.execute("SELECT name FROM currencies WHERE is_base = 1").fetchone()
        currencies = [r[0] for r in conn.execute(
            "SELECT name FROM currencies WHERE is_base = 0 ORDER BY sort, id")]
        table: dict[str, dict[str, float]] = {}
        for r in conn.execute("SELECT f.month, c.name, f.rate FROM fx_rates f JOIN currencies c ON c.id = f.currency_id"):
            table.setdefault(r[0], {})[r[1]] = r[2]
        tx = conn.execute("SELECT MIN(substr(date, 1, 7)), MAX(substr(date, 1, 7)) FROM transactions").fetchone()
        ends = [m.replace("/", "-") for m in tx if m] + [m for m in [start_month(conn)] if m] + list(table)
        this = f"{today.year}-{today.month:02d}"
        months = months_between(min(ends + [this]), max(ends + [this]))
    return {"base": base[0] if base else None, "currencies": currencies,
            "months": months[::-1], "rates": table}


def set_rate(month: str, currency: str, rate: float | None) -> None:
    """rate None clears it."""
    month = _check_month(month)
    with connect() as conn:
        cid = _currency_id(conn, currency)
        if conn.execute("SELECT is_base FROM currencies WHERE id = ?", (cid,)).fetchone()[0]:
            raise ValueError("本位币的汇率固定为 1，不用填")
        if rate is None:
            conn.execute("DELETE FROM fx_rates WHERE month = ? AND currency_id = ?", (month, cid))
            return
        rate = float(rate)
        if not rate > 0:
            raise ValueError("汇率要大于 0")
        conn.execute("INSERT INTO fx_rates (month, currency_id, rate) VALUES (?, ?, ?)"
                     " ON CONFLICT (month, currency_id) DO UPDATE SET rate = excluded.rate", (month, cid, rate))


# ---------- balance sheet ----------

def _rates_for(conn, month: str) -> dict[str, float]:
    """Rates of one month by currency name, the base currency included as 1."""
    out = {r[0]: r[1] for r in conn.execute(
        "SELECT c.name, f.rate FROM fx_rates f JOIN currencies c ON c.id = f.currency_id WHERE f.month = ?", (month,))}
    base = conn.execute("SELECT name FROM currencies WHERE is_base = 1").fetchone()
    if base:
        out[base[0]] = 1.0
    return out


def _holdings(conn, start: str, month: str) -> dict[tuple[str, str], float]:
    """(account, currency) -> balance at the end of `month`: the opening balances plus the
    transactions after the start month, up to and including `month`."""
    out: dict[tuple[str, str], float] = {}
    for r in conn.execute(
            "SELECT a.name, c.name, ob.amount FROM opening_balances ob"
            " JOIN accounts a ON a.id = ob.account_id JOIN currencies c ON c.id = ob.currency_id"):
        out[(r[0], r[1])] = r[2]
    if month > start:
        # Transaction dates are 2026/10/29 ...; months here are 2026-10
        lo, hi = start.replace("-", "/"), month.replace("-", "/")
        for r in conn.execute(
                "SELECT account, currency, SUM(amount) FROM transactions"
                " WHERE substr(date, 1, 7) > ? AND substr(date, 1, 7) <= ? GROUP BY account, currency", (lo, hi)):
            out[(r[0], r[1])] = out.get((r[0], r[1]), 0) + r[2]
    return {k: round(v, 2) for k, v in out.items()}


def _by_currency(holdings: dict[tuple[str, str], float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for (_, c), v in holdings.items():
        out[c] = round(out.get(c, 0) + v, 2)
    return out


def _convert(totals: dict[str, float], rates: dict[str, float]):
    """Each currency's total in the base currency (None when its rate is missing), and their
    sum (None if any non-zero total can't be converted)."""
    conv = {c: (round(v * rates[c], 2) if c in rates else None) for c, v in totals.items()}
    grand = None if any(v is None and totals[c] for c, v in conv.items()) else \
        round(sum(v or 0 for v in conv.values()), 2)
    return conv, grand


def balance_months(conn, today: date | None = None) -> list[str]:
    """Months the balance sheet can show, newest first: the start month to this month (or the
    last transaction, if later)."""
    start = start_month(conn)
    if not start:
        return []
    today = today or date.today()
    last = conn.execute("SELECT MAX(substr(date, 1, 7)) FROM transactions").fetchone()[0]
    end = max(f"{today.year}-{today.month:02d}", (last or "").replace("/", "-"), start)
    return months_between(start, end)[::-1]


def balances(month: str | None = None, today: date | None = None) -> dict:
    """The balance sheet for the end of one month (docs/plan.md, 账户结余):

    {"month", "start_month", "months" (to choose from), "base",
     "currencies": [columns, list order], "accounts": [{"name", "balances": {currency: amount}}],
     "rates": {currency: rate or None}, "totals": {currency: amount},
     "converted": {currency: in base or None}, "total": sum in base or None,
     "fx_gain": {currency: gain or None}, "fx_gain_total", "prev_total", "change"}

    fx_gain = last month's holding x (this month's rate - last month's rate); none for the
    start month itself. change = total - last month's total, which equals this month's
    converted income/expense plus fx_gain_total."""
    with connect() as conn:
        start = start_month(conn)
        months = balance_months(conn, today)
        if not start:
            return {"month": None, "start_month": None, "months": []}
        month = _check_month(month) if month else months[0]
        if month < start:
            raise ValueError(f"起始月是 {start}，更早的月份没有结余")

        holdings = _holdings(conn, start, month)
        rates = _rates_for(conn, month)
        base = conn.execute("SELECT name FROM currencies WHERE is_base = 1").fetchone()

        # Rows: accounts in list order, each with its currencies; columns: currencies in use
        order = [r[0] for r in conn.execute("SELECT name FROM currencies ORDER BY sort, id")]
        accounts = {r[0]: {"name": r[0], "balances": {}} for r in conn.execute(
            "SELECT name FROM accounts ORDER BY sort, id")}
        for r in conn.execute(
                "SELECT a.name, c.name FROM account_currencies ac"
                " JOIN accounts a ON a.id = ac.account_id JOIN currencies c ON c.id = ac.currency_id"):
            accounts[r[0]]["balances"][r[1]] = 0.0
        for (a, c), v in holdings.items():
            accounts.setdefault(a, {"name": a, "balances": {}})["balances"][c] = v
        used = {c for acc in accounts.values() for c in acc["balances"]}
        currencies = [c for c in order if c in used] + sorted(used - set(order))

        totals = {c: 0.0 for c in currencies} | _by_currency(holdings)
        converted, total = _convert(totals, rates)

        out = {"month": month, "start_month": start, "months": months, "base": base[0] if base else None,
               "currencies": currencies, "accounts": list(accounts.values()),
               "rates": {c: rates.get(c) for c in currencies}, "totals": totals,
               "converted": converted, "total": total,
               "fx_gain": {c: None for c in currencies}, "fx_gain_total": None, "prev_total": None, "change": None}

        if month > start:
            prev = _shift(month, -1)
            prev_totals = {c: 0.0 for c in currencies} | _by_currency(_holdings(conn, start, prev))
            prev_rates = _rates_for(conn, prev)
            gains = {}
            for c in currencies:
                held = prev_totals.get(c, 0)
                if not held:
                    gains[c] = 0.0
                elif c in rates and c in prev_rates:
                    gains[c] = round(held * (rates[c] - prev_rates[c]), 2)
                else:
                    gains[c] = None
            out["fx_gain"] = gains
            if all(g is not None for g in gains.values()):
                out["fx_gain_total"] = round(sum(gains.values()), 2)
            out["prev_total"] = _convert(prev_totals, prev_rates)[1]
            if total is not None and out["prev_total"] is not None:
                out["change"] = round(total - out["prev_total"], 2)
        return out
