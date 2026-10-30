"""Parse an Apple Wallet Suica transaction detail screenshot.

The top of the page looks like:
              ¥327
    品川駅 → 羽田空港第３ターミナル     <- route, or shop name
            交通機関                   <- category
     2026/10/25 21:29 (JST)
Below are a 合計 box (same amount again) and a map, which are ignored.
"""
import re

import numpy as np

from ..models import Tx
from ..ocr import Box

AMOUNT_RE = re.compile(r"^([+\-−]?)\s*[¥￥]\s*(\d[\d,]*)$")
DATETIME_RE = re.compile(r"(20\d{2})/(\d{1,2})/(\d{1,2})\s*(\d{1,2})[:：](\d{2})")
# OCR reads the screen's full-width digits as ASCII; convert them back
FULLWIDTH_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")


def _clean(text: str) -> str:
    text = re.sub(r"\s*→\s*", "->", text.strip())
    return text.translate(FULLWIDTH_DIGITS)


def parse_boxes(boxes: list[Box], img: np.ndarray | None = None) -> tuple[list[Tx], list[str]]:
    amounts = [b for b in boxes if AMOUNT_RE.match(b.text.replace(" ", ""))]
    if not amounts:
        return [], ["没有识别到金额，请上传 Apple 钱包里 Suica 的单笔交易详情页"]
    big = max(amounts, key=lambda b: b.y1 - b.y0)  # the large headline amount
    sign, num = AMOUNT_RE.match(big.text.replace(" ", "")).groups()

    dates = [b for b in boxes if b.y0 > big.y1 and DATETIME_RE.search(b.text)]
    if not dates:
        return [], ["没有识别到交易时间"]
    date_box = min(dates, key=lambda b: b.y0)
    year, month, day, hour, minute = map(int, DATETIME_RE.search(date_box.text).groups())

    # Lines between the amount and the date: description lines, then the category.
    lines = sorted((b for b in boxes if b.y0 > big.y1 and b.y1 < date_box.y0),
                   key=lambda b: b.y0)
    desc = lines[:-1] if len(lines) > 1 else lines
    merchant = _clean("".join(b.text for b in desc))

    # Rides and purchases are spending; a top-up (チャージ) adds money to the card.
    topup = sign == "+" or any("チャージ" in b.text for b in lines)
    value = float(num.replace(",", "")) * (1 if topup else -1)

    return [Tx(month=month, day=day, amount=value, merchant=merchant, year=year,
               hour=hour, minute=minute, sign_guessed=topup and sign != "+")], []
