"""Parse a Bank of China app 明细 (single transaction detail) screenshot.

The page is a label/value form:
    支出金额 (人民币元)          <- or 收入金额
    1,234.56
    对方名称     财付通-某某商户
    交易时间     2026/01/02 12:34:56
    记账时间     2026/01/02 12:34:56
    交易后余额   人民币元 9,876.54
The list page (账户详情) has no time of day, so only detail pages are supported.
"""
import re

import numpy as np

from ..models import Tx
from ..ocr import Box

AMOUNT_LABEL_RE = re.compile(r"(支出|收入)金额")
AMOUNT_RE = re.compile(r"^[+\-]?(\d[\d,]*\.\d{2})$")
DATETIME_RE = re.compile(r"(20\d{2})[/\-.](\d{1,2})[/\-.](\d{1,2})\s*(\d{1,2})[:：](\d{2})(?:[:：](\d{2}))?")
MONEY_RE = re.compile(r"(\d[\d,]*\.\d{2})")

def _value_of(boxes: list[Box], label: str) -> str | None:
    """Text to the right of the first box whose text starts with `label`."""
    for lb in boxes:
        if not lb.text.replace(" ", "").startswith(label):
            continue
        line_h = lb.y1 - lb.y0
        row = [b for b in boxes if b is not lb and b.x0 > lb.x1
               and abs(b.cy - lb.cy) < line_h * 0.6]
        if row:
            return min(row, key=lambda b: b.x0).text
    return None


def parse_boxes(boxes: list[Box], img: np.ndarray | None = None) -> tuple[list[Tx], list[str]]:
    # Amount: the big number below the 支出金额/收入金额 label.
    label = next((b for b in boxes if AMOUNT_LABEL_RE.search(b.text)), None)
    if label is None:
        return [], ["没有找到「支出金额/收入金额」，请上传中国银行的单笔交易明细页"]
    below = [b for b in boxes if b.y0 > label.cy and AMOUNT_RE.match(b.text.replace(" ", ""))]
    if not below:
        return [], ["没有识别到金额"]
    amt_box = min(below, key=lambda b: b.y0)
    value = float(AMOUNT_RE.match(amt_box.text.replace(" ", "")).group(1).replace(",", ""))
    if AMOUNT_LABEL_RE.search(label.text).group(1) == "支出":
        value = -value

    when = None
    for key in ("交易时间", "记账时间"):
        text = _value_of(boxes, key)
        if text and (m := DATETIME_RE.search(text)):
            when = m
            break
    if when is None:
        return [], ["没有识别到交易时间"]
    year, month, day, hour, minute, second = (int(g or 0) for g in when.groups())

    merchant = _value_of(boxes, "对方名称") or _value_of(boxes, "附言") or ""

    balance = None
    if (text := _value_of(boxes, "交易后余")) and (m := MONEY_RE.search(text)):
        balance = float(m.group(1).replace(",", ""))

    return [Tx(month=month, day=day, amount=value, merchant=merchant.strip(),
               year=year, hour=hour, minute=minute, second=second,
               balance=balance)], []
