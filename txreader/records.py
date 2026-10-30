"""Turn parsed transactions into CSV rows."""
from .models import Tx, resolve_date

WEEKDAYS_JA = "月火水木金土日"


def build_records(per_image: list[list[Tx]], default_year: int) -> list[dict]:
    """Merge results from several screenshots, dropping overlaps between them.

    Duplicates are only removed across images: two identical payments within
    one screenshot are kept. Rows come back oldest first. Apps list newest
    first, so rows sharing a timestamp are put in reverse screen order.
    """
    seen: dict[tuple, int] = {}
    out = []
    pos = 0  # position across all screenshots, top to bottom
    for txs in per_image:
        counts: dict[tuple, int] = {}
        for tx in txs:
            pos += 1
            r = resolve_date(tx, default_year)
            key = (r.dt, tx.amount, tx.merchant, tx.balance)
            counts[key] = counts.get(key, 0) + 1
            if counts[key] <= seen.get(key, 0):
                continue  # already seen in an earlier screenshot
            out.append({
                "dt": r.dt,
                "seq": -pos,
                "amount": tx.amount,
                "merchant": tx.merchant,
                "balance": tx.balance,
                "income": tx.amount > 0,
                "sign_guessed": tx.sign_guessed,
                "year_guessed": r.year_guessed,
            })
        for k, c in counts.items():
            seen[k] = max(seen.get(k, 0), c)
    out.sort(key=lambda r: (r["dt"], r["seq"]))
    for r in out:
        dt = r.pop("dt")
        del r["seq"]
        r["date"] = dt.strftime("%Y/%m/%d %H:%M:%S")
        r["weekday"] = WEEKDAYS_JA[dt.weekday()]
    return out
