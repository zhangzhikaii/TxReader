from datetime import date
from pathlib import Path

import json

import pytest

from txreader.ocr import decode_image
from txreader.records import build_records
from txreader.wechat import Tx, parse_image, resolve_year

# Private sample screenshot, kept out of git (see .gitignore)
SAMPLE = Path(__file__).parent.parent / "samples" / "example.PNG"
# [date, weekday, amount, merchant] rows expected from SAMPLE
EXPECTED_FILE = SAMPLE.with_name("example.expected.json")



@pytest.fixture(scope="module")
def img():
    if not SAMPLE.exists():
        pytest.skip("samples/example.PNG not present")
    return decode_image(SAMPLE.read_bytes())


@pytest.fixture(scope="module")
def expected():
    return [tuple(r) for r in json.loads(EXPECTED_FILE.read_text(encoding="utf-8"))]


def simplify(records):
    return [(r["date"], r["weekday"], r["amount"], r["merchant"]) for r in records]


def test_sample_screenshot(img, expected):
    records = build_records([parse_image(img)], 2026)
    assert simplify(records) == expected
    assert not any(r["year_guessed"] or r["sign_guessed"] for r in records)


def test_overlapping_screenshots_are_deduplicated(img, expected):
    h = img.shape[0]
    top = img[: int(h * 0.65)]       # header + first ~5 entries
    bottom = img[int(h * 0.35):]     # no header, overlaps the middle
    records = build_records([parse_image(top), parse_image(bottom)], 2026)
    assert simplify(records) == expected
    # rows only visible in the header-less half have a guessed year
    assert any(r["year_guessed"] for r in records)


def test_same_payment_twice_in_one_screenshot_is_kept():
    tx = Tx(2026, 10, 1, 12, 0, -10.0, "便利店")
    assert len(build_records([[tx, tx]], 2026)) == 2
    assert len(build_records([[tx, tx], [tx]], 2026)) == 2


def test_resolve_year_steps_back_for_future_dates():
    tx = Tx(None, 12, 31, 9, 0, -1.0, "x")
    assert resolve_year(tx, 2026, today=date(2026, 1, 5)) == 2025
    assert resolve_year(tx, 2024, today=date(2026, 1, 5)) == 2024
