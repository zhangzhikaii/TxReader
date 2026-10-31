"""Run every sample screenshot against its expected result.

Layout (samples/ is private and git-ignored, so these tests skip without it):
    samples/<source>/<name>.PNG
    samples/<source>/<name>.expected.json   # [[date, weekday, amount, merchant], ...]
<source> is a parser name (folder name is matched case-insensitively, e.g. BOC -> boc).
"""
import json
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from txreader.ocr import decode_image
from txreader.parsers import PARSERS, parse_image
from txreader.records import build_records

SAMPLES = Path(__file__).parent.parent / "samples"
CASES = sorted(
    p for p in SAMPLES.glob("*/*.expected.json") if p.parent.name.lower() in PARSERS
) if SAMPLES.exists() else []


def _image_for(expected: Path) -> Path:
    stem = expected.name.removesuffix(".expected.json")
    for ext in (".PNG", ".png", ".jpg", ".jpeg", ".JPG"):
        if (p := expected.with_name(stem + ext)).exists():
            return p
    raise FileNotFoundError(stem)


def _rows(records):
    return [[r["date"], r["weekday"], r["amount"], r["merchant"]] for r in records]


def _by_date(rows):
    """Expected files may list rows in any order (e.g. newest first, as on screen),
    and rows with identical timestamps have no meaningful order, so compare sorted."""
    return sorted(rows, key=lambda r: (r[0], r[3], float(r[2])))


def _load(case):
    source = case.parent.name.lower()
    img = decode_image(_image_for(case).read_bytes())
    expected = json.loads(case.read_text(encoding="utf-8"))
    year = int(expected[0][0][:4]) if expected else 2026
    return source, img, expected, year


if not CASES:
    pytest.skip("no private samples present", allow_module_level=True)


# Photos of paper receipts: shop names come from logos and odd fonts, so OCR often gets
# a character or two wrong and the user fixes it in the preview. Date and amount must
# still match exactly; the merchant only has to be close.
FUZZY_MERCHANT_SOURCES = {"receipt"}


def _close(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b).ratio() >= 0.6


@pytest.mark.parametrize("case", CASES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_sample(case):
    source, img, expected, year = _load(case)
    txs, warnings = parse_image(img, source)
    assert warnings == []
    got, want = _by_date(_rows(build_records([txs], year))), _by_date(expected)
    if source in FUZZY_MERCHANT_SOURCES:
        assert [r[:3] for r in got] == [r[:3] for r in want]
        assert all(_close(g[3], w[3]) for g, w in zip(got, want))
    else:
        assert got == want


# Sources whose screenshots are scrolling lists (BOC uses one detail page per transaction)
LIST_SOURCES = {"wechat", "icbc"}


@pytest.mark.parametrize("case", [c for c in CASES if c.parent.name.lower() in LIST_SOURCES],
                         ids=lambda p: f"{p.parent.name}/{p.name}")
def test_overlapping_halves_are_deduplicated(case):
    """Split the screenshot into two overlapping halves, as when scrolling."""
    source, img, expected, year = _load(case)
    h = img.shape[0]
    top, _ = parse_image(img[: int(h * 0.65)], source)
    bottom, _ = parse_image(img[int(h * 0.35):], source)
    assert _by_date(_rows(build_records([top, bottom], year))) == _by_date(expected)
