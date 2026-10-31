from datetime import date

from txreader.models import Tx, resolve_date
from txreader.ocr import Box
from txreader.parsers import receipt
from txreader.records import build_records


def test_same_payment_twice_in_one_screenshot_is_kept():
    tx = Tx(month=10, day=1, amount=-10.0, merchant="便利店", year=2026, hour=12, minute=0)
    assert len(build_records([[tx, tx]], 2026)) == 2
    assert len(build_records([[tx, tx], [tx]], 2026)) == 2


def test_balance_tells_identical_payments_apart():
    a = Tx(month=10, day=26, amount=-85.33, merchant="Octopus", year=2026, balance=8689.16)
    b = Tx(month=10, day=26, amount=-85.33, merchant="Octopus", year=2026, balance=8603.83)
    # second screenshot repeats only `b`
    assert len(build_records([[a, b], [b]], 2026)) == 2


def test_rows_without_time_keep_screen_order_oldest_first():
    newer = Tx(month=10, day=29, amount=-1.0, merchant="newer", year=2026)
    older = Tx(month=10, day=29, amount=-2.0, merchant="older", year=2026)
    rows = build_records([[newer, older]], 2026)  # apps list newest first
    assert [r["merchant"] for r in rows] == ["older", "newer"]
    assert rows[0]["date"] == "2026/10/29 00:00:00"


def test_missing_year_steps_back_for_future_dates():
    tx = Tx(month=12, day=31, amount=-1.0, merchant="x")
    assert resolve_date(tx, 2026, today=date(2026, 1, 5)).dt.year == 2025
    assert resolve_date(tx, 2024, today=date(2026, 1, 5)).dt.year == 2024


def _box(text, x0, y0, x1, y1, angle=0.0):
    return Box(text, x0, y0, x1, y1, 1.0, angle)


def test_receipt_total_on_curved_paper():
    # Left labels slope up to the right, right amounts slope down: the 合計 box sits
    # level with お預り's amount, but following each slope matches ¥1,520.
    boxes = [
        _box("某某商户", 300, 100, 700, 200),
        _box("某某店", 400, 220, 600, 280),
        _box("2026年 1月21日 21:01", 300, 320, 900, 380),
        _box("合計", 200, 1000, 400, 1120, -19),
        _box("¥1.520", 1000, 920, 1300, 1020, 6),
        _box("¥1，", 1000, 1010, 1150, 1110, 3),
        _box("600", 1140, 1010, 1300, 1110, 6),
        _box("お預り", 200, 1100, 400, 1220, -20),
    ]
    (tx,), warnings = receipt.parse_boxes(boxes)
    assert warnings == []
    assert (tx.year, tx.month, tx.day, tx.hour, tx.minute) == (2026, 1, 21, 21, 1)
    assert tx.amount == -1520
    assert tx.merchant == "某某商户某某店"  # branch-only store line gets the brand in front


def test_receipt_without_total_uses_tendered_minus_change():
    boxes = [
        _box("某某商户 某某駅前店", 100, 100, 700, 160),
        _box("令和8年2月3日", 100, 200, 400, 260),
        _box("12:34:56", 450, 200, 650, 260),
        _box("現金", 100, 500, 250, 560), _box("¥1,000", 600, 500, 800, 560),
        _box("お釣り", 100, 600, 250, 660), _box("¥120", 600, 600, 800, 660),
    ]
    (tx,), warnings = receipt.parse_boxes(boxes)
    assert warnings == []
    assert (tx.year, tx.month, tx.day, tx.hour, tx.minute, tx.second) == (2026, 2, 3, 12, 34, 56)
    assert tx.amount == -880
    assert tx.merchant == "某某商户 某某駅前店"
