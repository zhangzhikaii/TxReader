"""Parse a photo of a paper receipt (mostly cash purchases in Japan), one per photo.

Receipts come from many different shops, so nothing is tied to one layout.
A typical one looks like:
            某某商户                 <- logo / brand, often large
         某某商户 某某店              <- store name (line ending in 店)
     東京都某某区某某1-2-3            <- address, phone, 登録番号 ... (skipped)
     2026/01/18（日）21:19           <- date, time (seconds optional)
     商品A                 ¥1,580
     商品B                   ¥160
     小計                  ¥1,740
     合計       3点        ¥1,990    <- the amount we want
     お預り               ¥10,000
     お釣り                ¥8,010

- Date: the first "2026年1月21日" / "2026/01/18" / "2026-01-16" style date;
  the time is read from the same line or the line just below.
- Amount: the amount on the 合計 / Total line. Without one, お預り - お釣り,
  and as a last resort the largest yen amount (with a warning).
- Merchant: the first line above the date that ends in 店. When that is only a
  branch (e.g. 某某店), the logo line above it is put in front.

Photos are often tilted and the paper curved, so a label on the left and its
amount on the right are not at the same height. Each OCR box is extended along
its own slope and the two are compared halfway between them.
"""
import math
import re

import numpy as np

from ..models import Tx
from ..ocr import Box

DATE_RES = [
    re.compile(r"(?<!\d)(20\d{2})\s*[年/.\-]\s*(\d{1,2})\s*[月/.\-]\s*(\d{1,2})"),
    re.compile(r"令和\s*(\d{1,2}|元)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})"),
]
TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*[:：]\s*([0-5]\d)(?:\s*[:：]\s*([0-5]\d))?(?!\d)")
# ¥1,990 / ￥200- / 1,520円 / (¥133); OCR sometimes reads the comma as a period
AMOUNT_RE = re.compile(r"^([(（]?)([¥￥\\]?)(\d{1,3}(?:[,.]\d{3})+|\d+)(?:\.\d{1,2})?[-ー]?(円?)[)）]?$")

TOTAL_RE = re.compile(r"^(総?合計(金額)?|税込合計|お?会計(金額)?|お買上(げ)?(合計|計|金額)?|ご?請求(金)?額"
                      r"|領収(金)?額|お?支払(い)?(金)?額|total)", re.I)
NOT_TOTAL_RE = re.compile(r"小計|対象|点数|内税|税抜|subtotal", re.I)
TENDERED_RE = re.compile(r"預り|預かり|現金|cash", re.I)
CHANGE_RE = re.compile(r"釣|change", re.I)

# Header lines that are not the shop name: address, phone, tax number, etc.
NOISE_RE = re.compile(
    r"tel|電話|fax|〒|\d-\d|登録番号|インボイス|T\d{13}|領収|レシート|receipt|copy|様|no[.:\-]"
    r"|担当|レジ|端末|http|www|\.jp|\.com|適格請求書|ありがとう|\*{3}|丁目|番地|\dF$"
    r"|[都道府県市区町村].*\d",
    re.I)


def _cx(b: Box) -> float:
    return (b.x0 + b.x1) / 2


def _slope(b: Box) -> float:
    return math.tan(math.radians(max(-30.0, min(30.0, b.angle))))


def _line_height(b: Box) -> float:
    """Height of the text itself; a tilted box is taller than its text."""
    return max(20.0, (b.y1 - b.y0) - (b.x1 - b.x0) * abs(_slope(b)))


def _same_line(a: Box, b: Box) -> bool:
    xm = (_cx(a) + _cx(b)) / 2
    ya = a.cy + _slope(a) * (xm - _cx(a))
    yb = b.cy + _slope(b) * (xm - _cx(b))
    return abs(ya - yb) < 0.5 * max(_line_height(a), _line_height(b))


def _norm(text: str) -> str:
    return text.replace(" ", "").replace("　", "").replace("，", ",")


def _amount(text: str, need_yen: bool = False) -> int | None:
    """Parse an amount; parenthesised ones (tax notes like "(¥133)") are ignored."""
    m = AMOUNT_RE.match(_norm(text))
    if not m:
        return None
    paren, yen, num, en = m.groups()
    if paren or (need_yen and not (yen or en)):
        return None
    return int(re.sub(r"[,.]", "", num))


def _merge_split_amounts(boxes: list[Box]) -> list[Box]:
    """OCR sometimes splits "¥1,600" into "¥1，" and "600"; join such pairs."""
    out, used = [], set()
    for i, a in enumerate(boxes):
        if i in used:
            continue
        if re.search(r"(^[¥￥]|[,，.])$", _norm(a.text)):
            right = [(j, b) for j, b in enumerate(boxes)
                     if j != i and j not in used and b.x0 > _cx(a) and _same_line(a, b)
                     and b.x0 - a.x1 < 1.5 * _line_height(a) and re.match(r"\d", b.text)]
            if right:
                j, b = min(right, key=lambda jb: jb[1].x0)
                used.add(j)
                a = Box(a.text + b.text, min(a.x0, b.x0), min(a.y0, b.y0),
                        max(a.x1, b.x1), max(a.y1, b.y1), min(a.score, b.score), a.angle)
        out.append(a)
    return out


def _row_amount(label: Box, boxes: list[Box]) -> int | None:
    """The rightmost amount on the label's line (or inside the label box itself)."""
    m = re.search(r"[¥￥]?\d[\d,.，]*円?[-ー]?$", _norm(label.text))
    if m and (v := _amount(m.group(), need_yen=True)) is not None:
        return v
    row = [b for b in boxes if b is not label and b.x0 > label.x0 and _same_line(label, b)
           and _amount(b.text) is not None]
    return _amount(max(row, key=lambda b: b.x1).text) if row else None


def _find_date(boxes: list[Box]):
    for b in sorted(boxes, key=lambda b: b.y0):
        text = b.text.translate(str.maketrans("０１２３４５６７８９：", "0123456789:"))
        for i, rx in enumerate(DATE_RES):
            m = rx.search(text)
            if not m:
                continue
            y, mo, d = m.groups()
            year = int(y) if i == 0 else 2018 + (1 if y == "元" else int(y))
            if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
                return b, year, int(mo), int(d), text[m.end():]
    return None


def _find_time(date_box: Box, rest: str, boxes: list[Box]) -> tuple[int, int, int]:
    """Time after the date in the same box, else on the same line, else just below."""
    candidates = [rest]
    candidates += [b.text for b in sorted(boxes, key=lambda b: b.x0)
                   if b is not date_box and _same_line(date_box, b) and b.x0 > date_box.x0]
    h = _line_height(date_box)
    candidates += [b.text for b in sorted(boxes, key=lambda b: b.y0)
                   if date_box.cy < b.cy < date_box.y1 + 2 * h]
    for text in candidates:
        if m := TIME_RE.search(text):
            return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    return 0, 0, 0


def _find_total(boxes: list[Box]) -> tuple[int | None, list[str]]:
    labels = [b for b in boxes if TOTAL_RE.match(_norm(b.text)) and not NOT_TOTAL_RE.search(b.text)]
    amounts = [v for b in labels if (v := _row_amount(b, boxes)) is not None]
    if amounts:
        return max(amounts), []  # e.g. 税込合計 is larger than a pre-tax 合計

    def labelled(rx):
        vals = [v for b in boxes if rx.search(b.text) and (v := _row_amount(b, boxes)) is not None]
        return vals[0] if vals else None

    tendered, change = labelled(TENDERED_RE), labelled(CHANGE_RE)
    if tendered is not None and change is not None and tendered > change:
        return tendered - change, []

    yen = [v for b in boxes if (v := _amount(b.text, need_yen=True))]
    if yen:
        return max(yen), ["没有找到「合計」，金额取了小票上最大的数，请核对"]
    return None, ["没有识别到金额"]


def _find_merchant(header: list[Box]) -> str:
    lines = [b for b in sorted(header, key=lambda b: b.y0)
             if not NOISE_RE.search(b.text) and re.search(r"\w", b.text)]
    stores = [b for b in lines if re.sub(r"[\s)）]+$", "", b.text).endswith("店")]
    if stores:
        store = stores[0]
        name = store.text.strip()
        if len(name) <= 8:  # only a branch name, e.g. 大塚店: put the brand in front
            above = [b for b in lines if b.y1 <= store.cy]
            if above:
                name = max(above, key=lambda b: b.y1 - b.y0).text.strip() + name
        return name
    return max(lines, key=lambda b: b.y1 - b.y0).text.strip() if lines else ""


def parse_boxes(boxes: list[Box], img: np.ndarray | None = None) -> tuple[list[Tx], list[str]]:
    boxes = _merge_split_amounts([b for b in boxes if b.text])
    found = _find_date(boxes)
    if not found:
        return [], ["没有识别到日期，请拍清楚小票上的日期一行"]
    date_box, year, month, day, rest = found
    hour, minute, second = _find_time(date_box, rest, boxes)

    total, warnings = _find_total(boxes)
    if total is None:
        return [], warnings

    merchant = _find_merchant([b for b in boxes if b.y1 <= date_box.cy])
    merchant = re.sub(r"\s+", " ", merchant)
    if not merchant:
        warnings.append("没有识别到店名，请手动填写")

    return [Tx(month=month, day=day, amount=-float(total), merchant=merchant, year=year,
               hour=hour, minute=minute, second=second)], warnings
