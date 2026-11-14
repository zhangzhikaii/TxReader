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


# ---------- matching new rows against earlier uploads ----------

from txreader.matching import match_rows


def _row(date, amount, merchant, balance=None, guessed=False, id=None, status="pending"):
    return {"id": id, "date": date, "amount": amount, "merchant": merchant, "balance": balance,
            "date_guessed": guessed, "status": status}


def test_match_skips_confirmed_and_merges_pending():
    existing = [_row("2026/10/01 12:00:00", -10.0, "便利店", id=1, status="confirmed"),
                _row("2026/10/02 12:00:00", -20.0, "超市", id=2)]
    incoming = [_row("2026/10/01 12:00:00", -10.0, "便利店"),
                _row("2026/10/02 12:00:00", -20.0, "超市"),
                _row("2026/10/03 12:00:00", -30.0, "饭店")]
    assert match_rows(incoming, existing) == [("skip", 1), ("merge", 2), ("new", None)]


def test_match_counts_identical_payments():
    # Two identical payments that day were confirmed earlier; a third one is new
    existing = [_row("2026/10/01 12:00:00", -10.0, "便利店", id=i, status="confirmed") for i in (1, 2)]
    incoming = [_row("2026/10/01 12:00:00", -10.0, "便利店") for _ in range(3)]
    assert match_rows(incoming, existing) == [("skip", 1), ("skip", 2), ("new", None)]


def test_match_flags_same_date_and_amount_with_other_merchant():
    existing = [_row("2026/10/01 12:00:00", -10.0, "便利店", id=1, status="confirmed")]
    incoming = [_row("2026/10/01 12:00:00", -10.0, "便利居")]  # OCR read one character differently
    assert match_rows(incoming, existing) == [("suspect", 1)]


def test_match_exact_wins_over_lookalike():
    # The look-alike comes first on screen but must not take the row the exact copy matches
    existing = [_row("2026/10/01 12:00:00", -10.0, "便利店", id=1, status="confirmed")]
    incoming = [_row("2026/10/01 12:00:00", -10.0, "别的店"), _row("2026/10/01 12:00:00", -10.0, "便利店")]
    assert match_rows(incoming, existing) == [("new", None), ("skip", 1)]


def test_match_different_balance_is_a_different_payment():
    existing = [_row("2026/10/01 12:00:00", -10.0, "Octopus", balance=100.0, id=1)]
    assert match_rows([_row("2026/10/01 12:00:00", -10.0, "Octopus", balance=90.0)], existing) == [("new", None)]
    assert match_rows([_row("2026/10/01 12:00:00", -10.0, "Other", balance=90.0)], existing) == [("new", None)]


def test_match_guessed_dates_compare_day_and_time_only():
    # Year guessed as 2025 last time, 2026 now: still the same row
    existing = [_row("2025/10/01 12:00:00", -10.0, "便利店", guessed=True, id=1)]
    assert match_rows([_row("2026/10/01 12:00:00", -10.0, "便利店", guessed=True)], existing) == [("merge", 1)]
    # Without any guess the full date has to agree
    existing = [_row("2025/10/01 12:00:00", -10.0, "便利店", id=1)]
    assert match_rows([_row("2026/10/01 12:00:00", -10.0, "便利店")], existing) == [("new", None)]


def test_match_prefers_rows_with_known_dates():
    existing = [_row("2026/09/01 12:00:00", -10.0, "便利店", guessed=True, id=1),
                _row("2026/10/01 12:00:00", -10.0, "便利店", id=2, status="confirmed")]
    assert match_rows([_row("2026/10/01 12:00:00", -10.0, "便利店")], existing) == [("skip", 2)]


def test_build_records_tells_where_each_row_came_from():
    a = Tx(month=10, day=1, amount=-1.0, merchant="a", year=2026)
    b = Tx(month=10, day=2, amount=-2.0, merchant="b", year=2026)
    rows = build_records([[b, a], [a]], 2026)  # second screenshot only repeats `a`
    assert [(r["merchant"], r["img"], r["img_pos"]) for r in rows] == [("a", 0, 1), ("b", 0, 0)]


def test_quotes_each_runs_side_by_side():
    import threading
    from txreader import quotes
    barrier = threading.Barrier(3, timeout=2)  # breaks unless all three calls are in flight at once

    def fn(s):
        barrier.wait()
        if s == "BAD":
            raise quotes.QuoteError("连不上")
        return s.lower()
    out = quotes.each(fn, ["A", "BAD", "C"])
    assert [s for s, _ in out] == ["A", "BAD", "C"]
    assert out[0][1] == "a" and out[2][1] == "c"
    assert isinstance(out[1][1], quotes.QuoteError)
