"""Parse a Saison card app 明細 (statement) list screenshot.

The page looks like:
                       明細
    8月   9月   10月   [11月]   12月   1月      <- billing-month tabs (ignored)
    ┃ 某某商户 (某某→某某)                 540 円  >
    ┃ 8月26日                             1回払い
    ┃ 某某商户                           1,510 円  >
    ┃ 8月28日                             1回払い
Each entry is a name line with the amount on the right, then the date of use and
the payment plan (1回払い, リボ, ...; ignored). No year and no time are shown.
Charges are spending; a refund shows a minus sign and is taken as income.
"""
import re

import numpy as np

from ..models import Tx
from ..ocr import Box

AMOUNT_RE = re.compile(r"^([\-−–—]?)(\d[\d,]*)円$")
DATE_RE = re.compile(r"^(\d{1,2})月(\d{1,2})日$")


def _clean(text: str) -> str:
    # OCR puts a space before "(" on some lines and not others; drop spaces at brackets
    text = re.sub(r"\s*([()（）])\s*", r"\1", text.strip())
    return re.sub(r"\s*→\s*", "->", text)


def parse_boxes(boxes: list[Box], img: np.ndarray | None = None) -> tuple[list[Tx], list[str]]:
    amounts, dates = [], []
    for b in boxes:
        t = b.text.replace(" ", "")
        if m := AMOUNT_RE.match(t):
            amounts.append((b, m))
        elif m := DATE_RE.match(t):
            dates.append((b, m))
    if not amounts:
        return [], ["没有识别到金额，请上传セゾン（Saison）App「明細」列表的截图"]
    amounts.sort(key=lambda a: a[0].y0)
    dates.sort(key=lambda d: d[0].y0)

    txs = []
    prev_date_y = -1.0
    for d, dm in dates:
        # The entry's amount is the last one above this date and below the previous date.
        above = [(a, am) for a, am in amounts if prev_date_y < a.y0 < d.y0]
        top = prev_date_y
        prev_date_y = d.y1
        if not above:
            continue  # entry cut off at the top of the screenshot
        a, am = above[-1]
        # Name lines: left of the amount, from the amount's line down to the date.
        lo = max(top, a.y0 - (a.y1 - a.y0))
        name = [b for b in boxes
                if b.x1 < a.x0 and b.y0 >= lo and b.y1 <= d.y0 and b is not d]
        merchant = _clean("".join(b.text for b in sorted(name, key=lambda b: (b.y0, b.x0))))
        sign, num = am.groups()
        value = float(num.replace(",", "")) * (1 if sign else -1)
        txs.append(Tx(month=int(dm.group(1)), day=int(dm.group(2)),
                      amount=value, merchant=merchant))
    return txs, []
