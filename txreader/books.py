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
