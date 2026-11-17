"""OCR behind a one-method interface, so tests and other engines can stand in."""

import math
from typing import Protocol

import numpy as np
from PIL import Image

from doc_extract.ingestion.layout import Line


class OcrEngine(Protocol):
    def __call__(self, image: Image.Image) -> list[Line]:
        """Lines with boxes in image pixels."""


class RapidOcrEngine:
    """PaddleOCR models run with ONNX Runtime on CPU. The models ship inside the package."""

    def __init__(self) -> None:
        from rapidocr import RapidOCR  # loads models, so only when OCR is really needed

        self._engine = RapidOCR()

    def __call__(self, image: Image.Image) -> list[Line]:
        result = self._engine(np.array(image.convert("RGB")))
        if result.boxes is None:
            return []
        lines = []
        for corners, text, score in zip(result.boxes, result.txts, result.scores, strict=True):
            # Corners run clockwise from top-left, so the top edge gives the text angle.
            xs, ys = corners[:, 0], corners[:, 1]
            box = (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))
            dx, dy = corners[1] - corners[0]
            angle = math.degrees(math.atan2(float(dy), float(dx)))
            lines.append(Line(text, box, float(score), angle))
        return lines
