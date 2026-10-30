from datetime import date

from txreader.models import Tx, resolve_date
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
