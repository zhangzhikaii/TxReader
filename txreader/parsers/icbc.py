"""Parse an ICBC app 收支明细 (account history list) screenshot.

Entries are grouped under month headers like "2026年01月". Each entry:
    02   消费                                   -12.34
    周五  支付宝-某某商户...
         借记卡尾号1234 12:34:56            余额:5,678.90
The day/weekday label only appears beside the first entry of each day.
The note is "<first line>, <second line>", e.g. "消费, 支付宝-某某商户".
"""
import re
import statistics

import numpy as np

from ..models import Tx
from ..ocr import Box

HEADER_RE = re.compile(r"(20\d{2})年(\d{1,2})月")
DAY_RE = re.compile(r"^(\d{1,2})$")
WEEKDAY_RE = re.compile(r"^周([一二三四五六日天]?)$")
WEEKDAYS = {c: i for i, c in enumerate("一二三四五六日")} | {"天": 6}
AMOUNT_RE = re.compile(r"^([+\-−–—])\s*(\d[\d,]*\.\d{2})$")
TIME_RE = re.compile(r"(\d{2})[:：](\d{2})[:：](\d{2})\s*$")
BALANCE_RE = re.compile(r"余额[:：]?\s*([\d,]+\.\d{2})")


def _clean(text: str) -> str:
    return re.sub(r"\s*(\.{2,}|…+)$", "", text.strip())  # truncation marker


def _line_text(boxes: list[Box]) -> str:
    """Join boxes on one line, left to right (long names may be split in two)."""
    return " ".join(b.text for b in sorted(boxes, key=lambda b: b.x0))


def parse_boxes(boxes: list[Box], img: np.ndarray | None = None) -> tuple[list[Tx], list[str]]:
    if not boxes:
        return [], []
    width = max(b.x1 for b in boxes)
    left_col = width * 0.11  # day / weekday labels sit left of this

    headers, days, weekdays = [], [], []
    for b in boxes:
        t = b.text.replace(" ", "")
        if m := HEADER_RE.search(t):
            headers.append((b.cy, int(m.group(1)), int(m.group(2))))
        elif b.x1 < left_col and (m := DAY_RE.match(t)):
            days.append((b.cy, int(m.group(1))))
        elif b.x1 < left_col and (m := WEEKDAY_RE.match(t)):
            # OCR often drops the thin stroke of 一, leaving a bare 周
            weekdays.append((b.cy, WEEKDAYS[m.group(1) or "一"]))

    amounts = [b for b in boxes if b.x0 > width * 0.6
               and AMOUNT_RE.match(b.text.replace(" ", ""))]
    if not amounts:
        return [], []
    full_h = statistics.median(b.y1 - b.y0 for b in amounts)

    txs, undated = [], 0
    for a in amounts:
        line_h = a.y1 - a.y0
        if line_h < full_h * 0.8:
            continue  # entry is cut off at the top or bottom of the screenshot

        # Time line: the nearest box below ending in hh:mm:ss.
        times = [b for b in boxes if b.cy > a.y1 and b.y0 - a.y1 < full_h * 3
                 and b.x0 > left_col and TIME_RE.search(b.text)]
        if not times:
            continue  # cut off at the bottom
        time_box = min(times, key=lambda b: b.y0)
        hour, minute, second = map(int, TIME_RE.search(time_box.text).groups())

        # Title: left part of the amount's line. Description: lines between title and time.
        middle = [b for b in boxes if b.x0 > left_col and b.x1 < a.x0 and b is not time_box]
        title = [b for b in middle if abs(b.cy - a.cy) < line_h * 0.6]
        desc = [b for b in middle if b.cy > a.y1 - line_h * 0.2 and b.y1 <= time_box.y0 + 5
                and b not in title]
        if not title:
            continue
        merchant = ", ".join(t for t in (_clean(_line_text(title)), _clean(_line_text(desc))) if t)

        # Day: nearest day label at or above this entry, with no month header in between.
        ds = [d for d in days if d[0] < a.y1 + line_h]
        if not ds:
            undated += 1
            continue
        day_y, day = max(ds)
        wd = [w for w in weekdays if 0 <= w[0] - day_y < full_h * 1.5]
        weekday = min(wd)[1] if wd else None

        hs = [h for h in headers if h[0] < day_y]
        year, month = max(hs)[1:] if hs else (None, None)
        if month is None and weekday is None:
            undated += 1
            continue

        sign, num = AMOUNT_RE.match(a.text.replace(" ", "")).groups()
        value = float(num.replace(",", "")) * (1 if sign == "+" else -1)

        bal = [b for b in boxes if abs(b.cy - time_box.cy) < line_h * 0.6
               and BALANCE_RE.search(b.text.replace(" ", ""))]
        balance = (float(BALANCE_RE.search(bal[0].text.replace(" ", "")).group(1).replace(",", ""))
                   if bal else None)

        txs.append(Tx(month=month, day=day, amount=value, merchant=merchant, year=year,
                      hour=hour, minute=minute, second=second, weekday=weekday,
                      balance=balance))
    warnings = [f"{undated} 条交易看不到日期，已跳过（往上多截一点，让日期出现）"] if undated else []
    return txs, warnings
