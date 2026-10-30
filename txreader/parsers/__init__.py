"""Screenshot parsers, one per app. Each takes OCR boxes (+ image) and returns (transactions, warnings)."""
import numpy as np

from ..models import Tx
from ..ocr import run_ocr
from . import boc, wechat

PARSERS = {
    "wechat": wechat.parse_boxes,
    "boc": boc.parse_boxes,
}


def parse_image(img: np.ndarray, source: str) -> tuple[list[Tx], list[str]]:
    if source not in PARSERS:
        raise ValueError(f"不支持的来源：{source}")
    return PARSERS[source](run_ocr(img), img)
