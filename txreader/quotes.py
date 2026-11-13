"""Security prices for 持仓, from Yahoo Finance's public endpoints (no key, unofficial).

Only a symbol or the code typed in goes out, never quantities or amounts, and only when the
持仓 page asks (opening it, 「更新」, looking a code up).

- `search(code)`: candidate symbols for a code or name ("7203" -> 7203.T, 7203.SR ...)
- `latest(symbol)`: the latest price (during a session: the current one) and its time
- `closes(symbol, days)`: for each day, the close of the last trading day on or before it
- `price_for(symbol, week, this_week)`: this week -> latest; an ended week -> the close on its Sunday

Prices quoted in a currency's minor unit (GBp, ZAc, ILA) are turned into the major one.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote as urlquote

import httpx

# Two hosts serve the same API; the tries alternate between them. The search endpoint in
# particular often hangs, so the read timeout is short and a hang is just tried again.
HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
HEADERS = {"User-Agent": "Mozilla/5.0"}  # the endpoints refuse clients without one
TIMEOUT = httpx.Timeout(6, connect=5)
TRIES = 3
MINOR = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}

# ISO code -> what the currency list may call it (the list is free text)
CURRENCY_NAMES = {
    "JPY": ["日元", "日币", "日円", "円", "JPY"],
    "USD": ["美元", "美金", "USD"],
    "CNY": ["人民币", "RMB", "CNY"],
    "HKD": ["港币", "港元", "HKD"],
    "EUR": ["欧元", "EUR"],
    "GBP": ["英镑", "GBP"],
    "AUD": ["澳元", "澳币", "AUD"],
    "CAD": ["加元", "加币", "CAD"],
    "SGD": ["新加坡元", "新币", "SGD"],
    "KRW": ["韩元", "KRW"],
    "TWD": ["新台币", "台币", "TWD"],
    "CHF": ["瑞士法郎", "CHF"],
}


class QuoteError(Exception):
    """Yahoo Finance couldn't be reached or answered with an error."""


def currency_name(iso: str | None, names: list[str]) -> str | None:
    """The name in the currency list for an ISO code, if it has one."""
    for n in CURRENCY_NAMES.get(iso or "", []) + [iso or ""]:
        if n and n in names:
            return n
    return None


def _get(path: str, params: dict, tries: int = TRIES, timeout=TIMEOUT) -> dict | None:
    """The JSON of one call; None for 404 (no such symbol)."""
    for attempt in range(tries):  # hangs and dropped TLS connections happen now and then
        try:
            r = httpx.get(HOSTS[attempt % len(HOSTS)] + path, params=params, headers=HEADERS, timeout=timeout)
            break
        except httpx.TransportError as e:
            if attempt == tries - 1:
                raise QuoteError(f"连不上 Yahoo Finance：{e}") from None
            time.sleep(0.5)
    if r.status_code == 404 or (r.status_code == 400 and path.endswith("/search")):
        return None  # no such symbol; the search refuses some queries (Chinese names) with 400
    if r.status_code != 200:
        raise QuoteError(f"Yahoo Finance 返回 {r.status_code}")
    return r.json()


def _chart(symbol: str, **params) -> dict | None:
    """chart.result[0] ({"meta", "timestamp", "indicators"}), or None for an unknown symbol."""
    data = _get(f"/v8/finance/chart/{urlquote(symbol, safe='')}", params)
    result = ((data or {}).get("chart") or {}).get("result")
    return result[0] if result else None


def _search(q: str) -> list[dict]:
    # Not waited for long: lookup has the guesses to fall back on
    data = _get("/v1/finance/search", {"q": q, "quotesCount": 6, "newsCount": 0,
                                       "listsCount": 0, "enableFuzzyQuery": "false"},
                tries=2, timeout=httpx.Timeout(4, connect=4))
    return [x for x in (data or {}).get("quotes", []) if x.get("symbol")]


def _major(currency: str | None, price: float) -> tuple[str | None, float]:
    if currency in MINOR:
        iso, div = MINOR[currency]
        return iso, price / div
    return currency, price


def _epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def search(code: str) -> list[dict]:
    """[{"symbol", "name", "exchange", "type"}] best first."""
    return [{"symbol": x["symbol"], "name": x.get("longname") or x.get("shortname") or x["symbol"],
             "exchange": x.get("exchDisp") or x.get("exchange") or "", "type": x.get("typeDisp") or x.get("quoteType") or ""}
            for x in _search(code)]


def latest(symbol: str) -> dict | None:
    """{"symbol", "name", "currency", "price", "at" (UTC, ISO)} or None (unknown / no price)."""
    res = _chart(symbol, range="1d", interval="1d")
    meta = (res or {}).get("meta") or {}
    price = meta.get("regularMarketPrice")
    if price is None:
        return None
    currency, price = _major(meta.get("currency"), price)
    at = meta.get("regularMarketTime")
    return {"symbol": meta.get("symbol") or symbol, "name": meta.get("longName") or meta.get("shortName") or symbol,
            "currency": currency, "price": round(price, 4),
            "at": datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="minutes") if at else None}


def closes(symbol: str, days: list[date]) -> dict:
    """{"currency", "closes": {day: (price, trading day "2026-11-14")}}; a day before any data is left out.
    One call for all the days."""
    res = _chart(symbol, period1=_epoch(min(days) - timedelta(days=14)), period2=_epoch(max(days) + timedelta(days=2)),
                 interval="1d")
    if not res:
        return {"currency": None, "closes": {}}
    meta = res.get("meta") or {}
    off = meta.get("gmtoffset") or 0
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    series = sorted((datetime.fromtimestamp(t + off, timezone.utc).date(), c)
                    for t, c in zip(res.get("timestamp") or [], quote.get("close") or []) if c is not None)
    raw = meta.get("currency")
    out = {}
    for d in days:
        before = [s for s in series if s[0] <= d]
        if before:
            day, c = before[-1]
            out[d] = (round(_major(raw, c)[1], 4), day.isoformat())
    return {"currency": _major(raw, 0)[0], "closes": out}


def price_for(symbol: str, week: str, this_week: str) -> dict | None:
    """{"price", "currency", "source": "live" | "close", "at"} for a week's snapshot, or None."""
    if week >= this_week:
        q = latest(symbol)
        return q and {"price": q["price"], "currency": q["currency"], "source": "live", "at": q["at"]}
    sunday = date.fromisoformat(week) + timedelta(days=6)
    c = closes(symbol, [sunday])
    if sunday not in c["closes"]:
        return None
    price, day = c["closes"][sunday]
    return {"price": price, "currency": c["currency"], "source": "close", "at": day}


def guesses(code: str) -> list[str]:
    """Symbols a code may stand for, when the search can't be had: the code itself, and for
    numbers the usual exchanges (1-3 digits: Hong Kong, padded to 4; 4 digits: Tokyo, Hong Kong;
    5: Hong Kong; 6: Shanghai / Shenzhen)."""
    c = code.strip().upper()
    out = [c]
    if c.isdigit():
        if len(c) < 4:
            out += [c.zfill(4) + ".HK"]
        elif len(c) == 4:
            out += [c + ".T", c + ".HK"]
        elif len(c) == 5:
            out += [c[1:] + ".HK" if c.startswith("0") else c + ".HK"]
        elif len(c) == 6:
            out += [c + ".SS", c + ".SZ"] if c[0] in "569" else [c + ".SZ", c + ".SS"]
    return out


def lookup(code: str, week: str, this_week: str, names: list[str], limit: int = 5) -> list[dict]:
    """Candidates for a code with their price for the week: [{"symbol", "name", "exchange", "type",
    "currency" (ISO), "currency_name" (in the list, or None), "price", "source", "at"}]; the ones
    without a price are left out. The search (often slow) and the guesses run side by side; the
    guesses with an exchange (7203.T, 0700.HK) come first, as the search ranks numbers poorly
    ("00700" finds a Taiwan fund, not Tencent), then the search's, then the code itself if nothing else."""
    def priced(c):
        try:
            p = price_for(c["symbol"], week, this_week)
            if p and not c["name"]:
                q = latest(c["symbol"])
                c = c | {"name": q["name"] if q else c["symbol"]}
            return c, p, None
        except QuoteError as e:
            return c, None, e
    with ThreadPoolExecutor(max_workers=limit + 3) as pool:
        searching = pool.submit(search, code)
        guessed = {s: pool.submit(priced, {"symbol": s, "name": "", "exchange": "", "type": ""}) for s in guesses(code)}
        try:
            found, failed = searching.result()[:limit], None
        except QuoteError as e:
            found, failed = [], e
        by_search = {c["symbol"]: c for c in found}
        plain = code.strip().upper()
        # a guess already priced is reused, with the search's name and exchange when it found it too
        results = [(by_search.get(s, {}) | {"symbol": s, "name": by_search.get(s, {}).get("name") or g[0]["name"]}
                    if s in by_search else g[0], g[1], g[2])
                   for s, g in ((s, j.result()) for s, j in guessed.items()) if s != plain]
        results += [j.result() for j in [pool.submit(priced, c) for c in found if c["symbol"] not in guessed or c["symbol"] == plain]]
        if not any(p for _, p, _ in results):
            results.append(guessed[plain].result())
        seen = set()
        results = [r for r in results if not (r[0]["symbol"] in seen or seen.add(r[0]["symbol"]))]
    out = [c | p | {"currency_name": currency_name(p["currency"], names)} for c, p, _ in results if p]
    errors = [e for _, _, e in results if e]
    if not out and (failed or errors):  # nothing found because Yahoo Finance couldn't be reached
        raise failed or errors[0]
    return out
