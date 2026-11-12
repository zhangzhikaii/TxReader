"""Opening balances and monthly exchange rates: what the balance sheet and the monthly report
(docs/plan.md, steps 4 and 5) are computed from, together with the transactions.

- Opening balances: what each account held in each currency at the end of the start month.
  Later months follow from them plus the transactions after the start month.
- Monthly rates: typed in by hand, 1 unit of a currency = rate units of the base currency.
  The base currency itself is always 1 and not stored.
"""
import re
from datetime import date

from . import errors
from .db import connect, transaction
from .lists import type_list

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class NotFound(errors.NotFound):
    """A currency that is not on the list; raised with its name."""
    @property
    def detail(self):
        return f"没有币种「{self}」，请刷新页面"


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


def save_opening(balances: list[dict], start: str | None = None, set_start: bool = False) -> None:
    """The settings page's edits, saved together (all or nothing): the start month (when
    set_start) and balances [{"account_id", "currency", "amount" (None clears it)}]."""
    with transaction():
        if set_start:
            set_start_month(start)
        for b in balances:
            set_opening(b["account_id"], b["currency"], b.get("amount"))


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
        months = _all_months(conn, today, extra=list(table))
    return {"base": base[0] if base else None, "currencies": currencies,
            "months": months[::-1], "rates": table}


def _all_months(conn, today: date, extra=()) -> list[str]:
    """Oldest first: from the start month or the first transaction (whichever is earlier) to
    this month or the last transaction (whichever is later), stretched to cover `extra`."""
    tx = conn.execute("SELECT MIN(substr(date, 1, 7)), MAX(substr(date, 1, 7)) FROM transactions").fetchone()
    ends = [m.replace("/", "-") for m in tx if m] + [m for m in [start_month(conn)] if m] + list(extra)
    this = f"{today.year}-{today.month:02d}"
    return months_between(min(ends + [this]), max(ends + [this]))


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


def save_rates(rates: list[dict]) -> None:
    """Several rates [{"month", "currency", "rate" (None clears it)}], all or nothing."""
    with transaction():
        for r in rates:
            set_rate(r["month"], r["currency"], r.get("rate"))


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


def _holdings_on(conn, start: str, day: str) -> dict[tuple[str, str], float] | None:
    """Like `_holdings`, at the end of a day (2026/11/15) instead of a month. None when the day
    isn't after the start month: only the start month's end is known before that."""
    if day[:7].replace("/", "-") <= start:
        return None
    out = _holdings(conn, start, start)  # the opening balances
    for r in conn.execute(
            "SELECT account, currency, SUM(amount) FROM transactions"
            " WHERE substr(date, 1, 7) > ? AND substr(date, 1, 10) <= ? GROUP BY account, currency",
            (start.replace("-", "/"), day)):
        out[(r[0], r[1])] = round(out.get((r[0], r[1]), 0) + r[2], 2)
    return out


def _by_currency(holdings: dict[tuple[str, str], float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for (_, c), v in holdings.items():
        out[c] = round(out.get(c, 0) + v, 2)
    return out


def _sum(values):
    """The rounded sum, or None if any of the values couldn't be worked out (is None)."""
    values = list(values)
    return None if None in values else round(sum(values), 2)


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
     "currencies": [columns, list order],
     "accounts": [{"name", "type", "balances": {currency: amount}, "count": transactions this month}],
     "groups": [{"type", "label", "totals": {currency: amount}, "converted": {currency: in base or None},
                 "total": in base or None}] for the account types in use, in lists.ACCOUNT_TYPES order,
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
        accounts = {r[0]: {"name": r[0], "type": r[1], "balances": {}, "count": 0} for r in conn.execute(
            "SELECT name, type FROM accounts ORDER BY sort, id")}
        for r in conn.execute(
                "SELECT a.name, c.name FROM account_currencies ac"
                " JOIN accounts a ON a.id = ac.account_id JOIN currencies c ON c.id = ac.currency_id"):
            accounts[r[0]]["balances"][r[1]] = 0.0
        for (a, c), v in holdings.items():
            accounts.setdefault(a, {"name": a, "type": "", "balances": {}, "count": 0})["balances"][c] = v
        # For the page's "only accounts with something this month" (any currency)
        for r in conn.execute("SELECT account, COUNT(*) FROM transactions WHERE substr(date, 1, 7) = ?"
                              " GROUP BY account", (month.replace("-", "/"),)):
            if r[0] in accounts:
                accounts[r[0]]["count"] = r[1]
        used = {c for acc in accounts.values() for c in acc["balances"]}
        currencies = [c for c in order if c in used] + sorted(used - set(order))

        totals = {c: 0.0 for c in currencies} | _by_currency(holdings)
        converted, total = _convert(totals, rates)

        # Subtotals by account type (every account counts, shown on the page or not)
        groups = []
        for t in type_list():
            members = [acc for acc in accounts.values() if (acc["type"] or "") == t["key"]]
            if not members:
                continue
            sub = {c: round(sum(acc["balances"].get(c, 0) for acc in members), 2) for c in currencies}
            sub_conv, sub_total = _convert(sub, rates)
            groups.append({"type": t["key"], "label": t["label"], "totals": sub,
                           "converted": sub_conv, "total": sub_total})

        out = {"month": month, "start_month": start, "months": months, "base": base[0] if base else None,
               "currencies": currencies, "accounts": list(accounts.values()), "groups": groups,
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
            out["fx_gain_total"] = _sum(gains.values())
            out["prev_total"] = _convert(prev_totals, prev_rates)[1]
            if total is not None and out["prev_total"] is not None:
                out["change"] = round(total - out["prev_total"], 2)
        return out


# ---------- monthly report ----------

def monthly(month: str | None = None, today: date | None = None, year: str | None = None) -> dict:
    """Income and expense of one month by category and currency (docs/plan.md, 月度收支):

    {"period": "month", "month", "months" (to choose from, newest first), "years" (likewise),
     "base", "currencies": [columns],
     "categories": [{"name", "amounts": {currency: sum}, "converted": in base or None,
                     "count": number of transactions}],
     "totals": {currency: sum}, "converted": {currency: in base or None}, "converted_total",
     "rates": {currency: rate or None},
     "fx_gain": {currency: gain or None} or None, "fx_gain_total", "fx_note",
     "base_total": {currency: converted + fx_gain}, "base_grand_total",
     "balance_change": the balance sheet's change for the month, "check": "ok" / "diff" / None,
     "check_total": the base_grand_total compared, "check_diff": [months that disagree],
     "missing_rates": {currency: [months with flows but no rate]}}

    Sums are in each currency as booked (no rate involved); converted = sum x this month's rate.
    The FX gain comes from the balance sheet (last month's holding x the rate change), so
    converted + FX gain should equal the balance sheet's change; "check" says whether it does.

    With `year` instead, the same for a whole year: see `_yearly`."""
    today = today or date.today()
    if year is not None:
        return _yearly(year, today)
    with connect() as conn:
        months = _all_months(conn, today)[::-1]
        month = _check_month(month) if month else months[0]
        start = start_month(conn)
        base = conn.execute("SELECT name FROM currencies WHERE is_base = 1").fetchone()
        rates = _rates_for(conn, month)
        flows: dict[str, dict[str, float]] = {}
        counts: dict[str, int] = {}
        for r in conn.execute(
                "SELECT category, currency, SUM(amount), COUNT(*) FROM transactions WHERE substr(date, 1, 7) = ?"
                " GROUP BY category, currency", (month.replace("-", "/"),)):
            flows.setdefault(r[0], {})[r[1]] = round(r[2], 2)
            counts[r[0]] = counts.get(r[0], 0) + r[3]
        cat_order = [r[0] for r in conn.execute("SELECT name FROM categories ORDER BY sort, id")]
        cur_order = [r[0] for r in conn.execute("SELECT name FROM currencies ORDER BY sort, id")]

    used = {c for amounts in flows.values() for c in amounts}
    currencies = cur_order + sorted(used - set(cur_order))
    names = cat_order + sorted(set(flows) - set(cat_order))
    conv = lambda c, v: round(v * rates[c], 2) if c in rates else None

    categories = []
    for name in names:
        amounts = flows.get(name, {})
        categories.append({"name": name, "amounts": amounts, "count": counts.get(name, 0),
                           "converted": _sum(conv(c, v) for c, v in amounts.items())})
    totals = {c: round(sum(f.get(c, 0) for f in flows.values()), 2) for c in currencies}
    converted, converted_total = _convert(totals, rates)
    out = {"period": "month", "month": month, "months": months, "years": _years(months),
           "base": base[0] if base else None, "currencies": currencies,
           "categories": categories, "totals": totals, "converted": converted, "converted_total": converted_total,
           "rates": {c: rates.get(c) for c in currencies},
           "fx_gain": None, "fx_gain_total": None, "fx_note": None,
           "base_total": {}, "base_grand_total": None, "balance_change": None, "check": None,
           "check_total": None, "check_diff": [],
           "missing_rates": {c: [month] for c in currencies if totals[c] and c not in rates}}

    # The FX gain needs last month's holdings, so only after the start month
    if not start:
        out["fx_note"] = "没有设起始月，算不出汇差（设置 › 期初余额）"
    elif month <= start:
        out["fx_note"] = "起始月和之前的月份没有上月结余，算不出汇差"
    else:
        sheet = balances(month, today)
        out["fx_gain"] = {c: sheet["fx_gain"].get(c, 0.0) for c in currencies}
        out["fx_gain_total"] = sheet["fx_gain_total"]
        out["balance_change"] = sheet["change"]

    gains = out["fx_gain"] or {}
    for c in currencies:
        v, g = converted[c], gains.get(c, 0.0)
        out["base_total"][c] = None if v is None and totals[c] or g is None else round((v or 0) + g, 2)
    out["base_grand_total"] = _sum(out["base_total"].values())
    if out["base_grand_total"] is not None and out["balance_change"] is not None:
        # Each cell is rounded on its own, so allow a few cents
        out["check"] = "ok" if abs(out["base_grand_total"] - out["balance_change"]) < 0.05 else "diff"
        out["check_total"] = out["base_grand_total"]
        out["check_diff"] = [month] if out["check"] == "diff" else []
    return out


def _years(months: list[str]) -> list[str]:
    return sorted({m[:4] for m in months}, reverse=True)


def _yearly(year: str, today: date) -> dict:
    """`monthly` for every month of a year (within the months there are), added up. Each month
    is converted at its own rate. The FX gain and the check only cover months after the start
    month, as in each month's report; the check is done when every one of those months can be
    checked, and compares their totals with the balance sheet's changes over them."""
    if not isinstance(year, str) or not re.fullmatch(r"\d{4}", year):
        raise ValueError(f"年份格式不对：{year}，应为 2026")
    with connect() as conn:
        inside = [m for m in _all_months(conn, today) if m[:4] == year]
    if not inside:
        raise ValueError(f"{year} 年没有数据")
    reps = [monthly(m, today) for m in inside]
    last = reps[-1]

    currencies = []
    for r in reps:
        currencies += [c for c in r["currencies"] if c not in currencies]
    cats: dict[str, dict] = {}
    for r in reps:
        for c in r["categories"]:
            row = cats.setdefault(c["name"], {"name": c["name"], "amounts": {}, "count": 0, "conv": []})
            for cur, v in c["amounts"].items():
                row["amounts"][cur] = round(row["amounts"].get(cur, 0) + v, 2)
            row["count"] += c["count"]
            row["conv"].append(c["converted"])
    categories = [{"name": c["name"], "amounts": c["amounts"], "count": c["count"],
                   "converted": _sum(c["conv"])} for c in cats.values()]
    # A currency with nothing in a month counts as 0 there, even without that month's rate
    per = lambda key, c: [r[key].get(c, 0.0) if r["totals"].get(c) else 0.0 for r in reps]
    totals = {c: round(sum(per("totals", c)), 2) for c in currencies}
    converted = {c: _sum(per("converted", c)) for c in currencies}

    out = {"period": "year", "year": year, "month": None, "months": last["months"], "years": last["years"],
           "base": last["base"], "currencies": currencies, "categories": categories,
           "totals": totals, "converted": converted, "converted_total": _sum(r["converted_total"] for r in reps),
           "rates": {}, "fx_gain": None, "fx_gain_total": None, "fx_note": None,
           "base_total": {c: _sum(r["base_total"].get(c, 0.0) for r in reps) for c in currencies},
           "base_grand_total": _sum(r["base_grand_total"] for r in reps),
           "balance_change": None, "check": None, "check_total": None, "check_diff": [], "missing_rates": {}}
    for r in reps:
        for c, ms in r["missing_rates"].items():
            out["missing_rates"].setdefault(c, []).extend(ms)

    with_fx = [r for r in reps if r["fx_gain"] is not None]
    if not with_fx:
        out["fx_note"] = reps[0]["fx_note"]
    else:
        out["fx_gain"] = {c: _sum(r["fx_gain"].get(c, 0.0) for r in with_fx) for c in currencies}
        out["fx_gain_total"] = _sum(r["fx_gain_total"] for r in with_fx)
        if len(with_fx) < len(reps):
            out["fx_note"] = f"起始月和之前的月份没有汇差，汇差从 {with_fx[0]['month']} 算起"
        if all(r["check"] for r in with_fx):
            out["check_total"] = _sum(r["check_total"] for r in with_fx)
            out["balance_change"] = _sum(r["balance_change"] for r in with_fx)
            out["check_diff"] = [r["month"] for r in with_fx if r["check"] == "diff"]
            out["check"] = "diff" if out["check_diff"] else "ok"
    return out


# ---------- month-by-month comparison ----------

TRANSFER = "内部转账"  # moves money between accounts; not income or expense


def trend(end: str | None = None, span: int = 6, currency: str | None = None,
          today: date | None = None) -> dict:
    """Every category month by month over the `span` months up to `end` (docs/plan.md, 每月对比);
    the page picks which ones to compare.

    {"end", "months": [the window, oldest first], "choices": [ends to choose from, newest first],
     "span", "currency": one currency as booked, or None = all converted to the base currency,
     "currencies": [to choose from], "base", "current": the month still in progress or None,
     "basis": months the averages use, "categories": [rows], "missing_rates": [months whose
     conversion is incomplete]}

    A row is {"name", "values": {month: sum, or None when a rate is missing}, "months": how many
    basis months it has transactions in, "average": over those months, "count", "fixed": marked in
    settings to be compared by default}. Rows are every
    category on the list (in list order, with or without transactions), then any others that
    have transactions. The month still in progress is shown but left out of the basis, as it
    would pull averages down; the internal transfer category is left out altogether."""
    if span not in (3, 6, 12):
        raise ValueError("月数只能是 3、6 或 12")
    today = today or date.today()
    this = f"{today.year}-{today.month:02d}"
    with connect() as conn:
        choices = _all_months(conn, today)[::-1]
        end = _check_month(end) if end else choices[0]
        months = [m for m in months_between(_shift(end, 1 - span), end) if m >= choices[-1]] or [end]
        base = conn.execute("SELECT name FROM currencies WHERE is_base = 1").fetchone()
        cur_order = [r[0] for r in conn.execute("SELECT name FROM currencies ORDER BY sort, id")]
        cat_order, fixed = [], set()
        for r in conn.execute("SELECT name, fixed FROM categories ORDER BY sort, id"):
            cat_order.append(r[0])
            if r[1]:
                fixed.add(r[0])
        rates = {m: _rates_for(conn, m) for m in months}
        # (category, currency, month) -> sum
        flows: dict[tuple[str, str, str], float] = {}
        counts: dict[str, int] = {}
        lo, hi = months[0].replace("-", "/"), months[-1].replace("-", "/")
        for r in conn.execute(
                "SELECT category, currency, substr(date, 1, 7), SUM(amount), COUNT(*) FROM transactions"
                " WHERE substr(date, 1, 7) BETWEEN ? AND ? AND category != ? GROUP BY 1, 2, 3", (lo, hi, TRANSFER)):
            flows[(r[0], r[1], r[2].replace("/", "-"))] = r[3]
            if currency is None or r[1] == currency:
                counts[r[0]] = counts.get(r[0], 0) + r[4]

    used = {c for _, c, _ in flows}
    currencies = [c for c in cur_order if c in used] + sorted(used - set(cur_order))
    if currency is not None and currency not in currencies + cur_order:
        raise NotFound(currency)

    # One value per category and month: that currency's sum, or every currency converted
    values: dict[str, dict[str, float | None]] = {}
    missing = set()
    for (cat, cur, month), v in flows.items():
        if currency is not None:
            if cur == currency:
                values.setdefault(cat, {})[month] = v
            continue
        row = values.setdefault(cat, {})
        if cur in rates[month] and row.get(month, 0) is not None:
            row[month] = row.get(month, 0) + v * rates[month][cur]
        elif v:
            row[month] = None
            missing.add(month)
        else:
            row.setdefault(month, 0)
    values = {cat: {m: (None if v is None else round(v, 2)) for m, v in row.items()} for cat, row in values.items()}

    current = this if this in months else None
    basis = [m for m in months if m != current] or months

    def row(name):
        vals = values.get(name, {})
        seen = [m for m in basis if m in vals]
        known = [vals[m] for m in seen if vals[m] is not None]
        return {"name": name, "values": vals, "months": len(seen), "count": counts.get(name, 0),
                "average": round(sum(known) / len(known), 2) if known else None, "fixed": name in fixed}

    names = [n for n in cat_order if n != TRANSFER] + sorted(set(values) - set(cat_order))
    return {"end": end, "months": months, "choices": choices, "span": span, "currency": currency,
            "currencies": currencies, "base": base[0] if base else None, "current": current,
            "basis": basis, "categories": [row(n) for n in names], "missing_rates": sorted(missing)}
