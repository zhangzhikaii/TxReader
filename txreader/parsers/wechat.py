"""Parse a WeChat 零钱明细 (wallet transaction list) screenshot.

Each entry on screen looks like:
    [icon]  商户名                 -87.00
            10月29日 10:54     零钱余额 18.48
Month headers like "2026年10月" give the year.
"""
import re

import numpy as np

from ..models import Tx
from ..ocr import Box

DATE_RE = re.compile(r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2})[:：](\d{2})")
HEADER_RE = re.compile(r"(20\d{2})年(\d{1,2})月")
# OCR sometimes reads the minus sign as a dash or the Chinese character 一
AMOUNT_RE = re.compile(r"^([+\-−–—一]?)\s*¥?\s*(\d[\d,]*\.\d{2})$")
BALANCE_RE = re.compile(r"余额\s*([\d,]+\.\d{2})")


def _is_income_colour(img: np.ndarray, b: Box) -> bool:
    """WeChat draws incomes in orange/yellow and expenses in black."""
    crop = img[int(b.y0):int(b.y1), int(b.x0):int(b.x1)]
    if crop.size == 0:
        return False
    px = crop.reshape(-1, 3).astype(int)
    # keep only "ink" pixels (not the white background)
    ink = px[px.min(axis=1) < 200]
    if len(ink) == 0:
        return False
    bgr = ink.mean(axis=0)
    return bgr[2] - bgr[0] > 60  # red channel much higher than blue -> orange


def parse_boxes(boxes: list[Box], img: np.ndarray | None = None) -> tuple[list[Tx], list[str]]:
    headers = []
    for b in boxes:
        m = HEADER_RE.search(b.text)
        if m:
            headers.append((b.cy, int(m.group(1)), int(m.group(2))))

    txs = []
    for d in boxes:
        m = DATE_RE.search(d.text.replace(" ", ""))
        if not m:
            continue
        month, day, hour, minute = map(int, m.groups())
        line_h = d.y1 - d.y0

        # Title row: the line directly above the date line, starting at roughly the same x.
        above = [b for b in boxes
                 if b is not d and b.y1 <= d.y0 + line_h * 0.3
                 and d.y0 - b.y1 < line_h * 1.5]
        titles = [b for b in above if abs(b.x0 - d.x0) < line_h * 1.5]
        amounts = [b for b in above if AMOUNT_RE.match(b.text.replace(" ", ""))
                   and b.x0 > d.x1]
        if not titles or not amounts:
            continue  # entry is cut off at the top of the screenshot
        title = max(titles, key=lambda b: b.y1)
        amt_box = min(amounts, key=lambda b: abs(b.cy - title.cy))

        sign, num = AMOUNT_RE.match(amt_box.text.replace(" ", "")).groups()
        value = float(num.replace(",", ""))
        guessed = False
        if sign == "+":
            pass
        elif sign:
            value = -value
        else:
            guessed = True
            if img is None or not _is_income_colour(img, amt_box):
                value = -value

        # Year comes from the nearest month header above this entry.
        year = None
        hs = [h for h in headers if h[0] < d.cy]
        if hs:
            _, hy, hm = max(hs, key=lambda h: h[0])
            year = hy if hm == month else None

        bal = [b for b in boxes if abs(b.cy - d.cy) < line_h * 0.6
               and BALANCE_RE.search(b.text.replace(" ", ""))]
        balance = (float(BALANCE_RE.search(bal[0].text.replace(" ", "")).group(1).replace(",", ""))
                   if bal else None)

        txs.append(Tx(month=month, day=day, amount=value, merchant=title.text,
                      year=year, hour=hour, minute=minute, balance=balance,
                      sign_guessed=guessed))
    return txs, []
