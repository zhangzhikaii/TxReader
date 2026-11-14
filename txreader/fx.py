"""月汇率 from Yahoo Finance (see quotes.py): 1 foreign currency = how much of the base currency.

- A currency gets its rates from there when it and the base currency both have a code
  (currencies.code, ISO 4217; set on the settings page). The symbol is e.g. JPYCNY=X; only
  that goes out, never amounts.
- This month: the latest rate (source "live"), fetched again when the rates page is opened and
  it is older than STALE, or right away with 「更新汇率」. An ended month: the close of its last
  trading day ("close"), fetched once and then final. A rate typed in ("manual") is never
  overwritten; clearing it lets the next sync fetch it.
- Months are the rate table's (books._all_months), up to this month. Nothing runs in the
  background: `sync` is called when the rates tab, 结余 or 收支 is opened, `refresh` by 「更新汇率」.
  All the currencies' calls run side by side (quotes.each).
"""
from datetime import date, datetime, timedelta, timezone

from . import books, quotes
from .db import connect, transaction

STALE = timedelta(minutes=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def month_end(month: str) -> date:
    return date.fromisoformat(books._shift(month, 1) + "-01") - timedelta(days=1)


def sync(today: date | None = None, force: bool = False) -> dict:
    """Fill in what is missing, turn ended months' live rates into their close, fetch this
    month's live rates again when stale (always with `force`).
    {"changed": bool, "updated": n, "warnings": [...]}. A rate that can't be had stays as it
    was (or missing) and is tried again next time."""
    today = today or date.today()
    this = f"{today.year}-{today.month:02d}"
    warnings = []
    with connect() as conn:
        base = conn.execute("SELECT name, code FROM currencies WHERE is_base = 1").fetchone()
        coded = [dict(r) for r in conn.execute(
            "SELECT id, name, code FROM currencies WHERE is_base = 0 AND code != '' ORDER BY sort, id")]
        if not base or not coded:
            return {"changed": False, "updated": 0, "warnings": []}
        if not base["code"]:
            return {"changed": False, "updated": 0,
                    "warnings": [f"本位币{base['name']}没有代码，取不了汇率（在「设置 › 币种」里填上 CNY）"]}
        months = [m for m in books._all_months(conn, today) if m <= this]
        have = {(r[0], r[1]): (r[2], r[3]) for r in conn.execute("SELECT month, currency_id, source, rate_at FROM fx_rates")}
    cutoff = (_now() - STALE).isoformat(timespec="minutes")
    got = {}  # (month, currency id) -> (rate, source, at)
    plan = []  # (currency, symbol, label, ended months, live?)
    for c in coded:
        if c["code"] == base["code"]:
            continue
        ended = [m for m in months if m < this and have.get((m, c["id"]), ("live",))[0] == "live"]
        live = this in months and (
            (row := have.get((this, c["id"]))) is None
            or (row[0] == "live" and (force or (row[1] or "") < cutoff)))
        plan.append((c, quotes.fx_symbol(c["code"], base["code"]), f"{c['name']}（{c['code']}）", ended, live))
    # all the calls side by side, then the results in the currencies' order
    jobs = [("close", p[1], tuple(month_end(m) for m in p[3])) for p in plan if p[3]]
    jobs += [("live", p[1], ()) for p in plan if p[4]]
    fetched = dict(quotes.each(
        lambda j: quotes.closes(j[1], list(j[2]), digits=None) if j[0] == "close" else quotes.latest(j[1], digits=None),
        jobs))
    for c, symbol, label, ended, live in plan:
        if ended:
            res = fetched["close", symbol, tuple(month_end(m) for m in ended)]
            if isinstance(res, quotes.QuoteError):
                warnings.append(f"{label}：{res}")
            else:
                if res["closes"] and res["currency"] != base["code"]:
                    warnings.append(f"{label}：Yahoo Finance 给的是 {res['currency']}，不是 {base['code']}")
                    continue
                missing = [m for m in ended if month_end(m) not in res["closes"]]
                for m in ended:
                    if m not in missing:
                        rate, day = res["closes"][month_end(m)]
                        got[m, c["id"]] = (rate, "close", day)
                if missing:
                    warnings.append(f"{label}：Yahoo Finance 没有 {'、'.join(missing)} 的收盘价（代码 {symbol}）")
        if live:
            q = fetched["live", symbol, ()]
            if isinstance(q, quotes.QuoteError):
                warnings.append(f"{label}：{q}")
                continue
            if not q:
                warnings.append(f"{label}：Yahoo Finance 没有最新汇率（代码 {symbol}）")
            elif q["currency"] and q["currency"] != base["code"]:
                warnings.append(f"{label}：Yahoo Finance 给的是 {q['currency']}，不是 {base['code']}")
            else:
                got[this, c["id"]] = (q["price"], "live", q["at"] or _now().isoformat(timespec="minutes"))
    with transaction() as conn:
        for (month, cid), (rate, source, at) in got.items():
            # a rate typed in meanwhile wins
            conn.execute("INSERT INTO fx_rates (month, currency_id, rate, source, rate_at) VALUES (?, ?, ?, ?, ?)"
                         " ON CONFLICT (month, currency_id) DO UPDATE SET rate = excluded.rate,"
                         " source = excluded.source, rate_at = excluded.rate_at WHERE fx_rates.source != 'manual'",
                         (month, cid, rate, source, at))
    return {"changed": bool(got), "updated": len(got), "warnings": warnings}


def refresh(today: date | None = None) -> dict:
    """「更新汇率」: this month's live rates fetched again now (and anything missing filled in)."""
    return sync(today, force=True)
