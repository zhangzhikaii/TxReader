"""Turn parsed transactions into CSV rows."""
from datetime import datetime

from .wechat import Tx, resolve_year

WEEKDAYS_JA = "月火水木金土日"
CSV_HEADER = ["日期", "曜日", "账户", "币种", "金额", "种类", "描述"]


def build_records(per_image: list[list[Tx]], default_year: int) -> list[dict]:
    """Merge results from several screenshots, dropping overlaps between them.

    Duplicates are only removed across images: two identical payments in the
    same minute within one screenshot are kept.
    """
    seen: dict[tuple, int] = {}
    out = []
    for txs in per_image:
        counts: dict[tuple, int] = {}
        for tx in txs:
            dt = datetime(resolve_year(tx, default_year), tx.month, tx.day, tx.hour, tx.minute)
            key = (dt, tx.amount, tx.merchant)
            counts[key] = counts.get(key, 0) + 1
            if counts[key] <= seen.get(key, 0):
                continue  # already seen in an earlier screenshot
            out.append({
                "datetime": dt,
                "amount": tx.amount,
                "merchant": tx.merchant,
                "income": tx.amount > 0,
                "sign_guessed": tx.sign_guessed,
                "year_guessed": tx.year is None,
            })
        for k, c in counts.items():
            seen[k] = max(seen.get(k, 0), c)
    out.sort(key=lambda r: r["datetime"])
    for r in out:
        dt = r.pop("datetime")
        r["date"] = dt.strftime("%Y/%m/%d %H:%M:%S")
        r["weekday"] = WEEKDAYS_JA[dt.weekday()]
    return out
