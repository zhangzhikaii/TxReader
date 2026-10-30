"""Thin wrapper around RapidOCR: image bytes -> list of text boxes."""
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np


@dataclass
class Box:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    score: float

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@lru_cache(maxsize=1)
def _engine():
    from rapidocr import RapidOCR
    return RapidOCR()


def decode_image(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("无法读取图片")
    return img


def run_ocr(img: np.ndarray) -> list[Box]:
    result = _engine()(img)
    if result.txts is None:
        return []
    boxes = []
    for pts, text, score in zip(result.boxes, result.txts, result.scores):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        boxes.append(Box(text.strip(), min(xs), min(ys), max(xs), max(ys), float(score)))
    return boxes
