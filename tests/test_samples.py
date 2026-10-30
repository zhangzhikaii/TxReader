"""Run every sample screenshot against its expected result.

Layout (samples/ is private and git-ignored, so these tests skip without it):
    samples/<source>/<name>.PNG
    samples/<source>/<name>.expected.json   # [[date, weekday, amount, merchant], ...]
<source> is a parser name (folder name is matched case-insensitively, e.g. BOC -> boc).
"""
import json
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


def _load(case):
    source = case.parent.name.lower()
    img = decode_image(_image_for(case).read_bytes())
    expected = json.loads(case.read_text(encoding="utf-8"))
    year = int(expected[0][0][:4]) if expected else 2026
    return source, img, expected, year


if not CASES:
    pytest.skip("no private samples present", allow_module_level=True)


@pytest.mark.parametrize("case", CASES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_sample(case):
    source, img, expected, year = _load(case)
    txs, warnings = parse_image(img, source)
    assert warnings == []
    assert _rows(build_records([txs], year)) == expected


# Sources whose screenshots are scrolling lists (BOC uses one detail page per transaction)
LIST_SOURCES = {"wechat"}


@pytest.mark.parametrize("case", [c for c in CASES if c.parent.name.lower() in LIST_SOURCES],
                         ids=lambda p: f"{p.parent.name}/{p.name}")
def test_overlapping_halves_are_deduplicated(case):
    """Split the screenshot into two overlapping halves, as when scrolling."""
    source, img, expected, year = _load(case)
    h = img.shape[0]
    top, _ = parse_image(img[: int(h * 0.65)], source)
    bottom, _ = parse_image(img[int(h * 0.35):], source)
    assert _rows(build_records([top, bottom], year)) == expected
