"""OCR wrapper (RapidOCR / onnxruntime, CPU, offline).

Why RapidOCR: private deployment forbids cloud OCR APIs, and PaddleOCR's paddlepaddle wheels are
heavy for a 2C4G ECS. RapidOCR ships ONNX models (~15MB) and runs on onnxruntime CPU with
acceptable Chinese accuracy. If the dependency is missing the caller degrades explicitly
(`OcrUnavailable`) instead of pretending the image had no text.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .types import BBox, Element


class OcrUnavailable(RuntimeError):
    """Raised when the OCR engine cannot be loaded/executed; callers must degrade."""


@dataclass
class OcrOutput:
    elements: list[Element]
    engine: str
    warnings: list[str]


_lock = threading.Lock()
_engine = None
_engine_error: str | None = None


def _get_engine():
    global _engine, _engine_error
    if _engine is not None:
        return _engine
    if _engine_error is not None:
        raise OcrUnavailable(_engine_error)
    with _lock:
        if _engine is not None:
            return _engine
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore

            _engine = RapidOCR()
        except Exception as exc:  # noqa: BLE001 - degrade with the original reason
            _engine_error = f"rapidocr unavailable: {exc}"
            raise OcrUnavailable(_engine_error) from exc
    return _engine


def _boxes_to_bbox(raw) -> BBox:
    xs = [float(p[0]) for p in raw]
    ys = [float(p[1]) for p in raw]
    return [min(xs), min(ys), max(xs), max(ys)]


def ocr_image(data: bytes) -> OcrOutput:
    """Run OCR on encoded image bytes. Raises OcrUnavailable when the engine cannot run."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise OcrUnavailable(f"opencv/numpy unavailable: {exc}") from exc
    arr = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise OcrUnavailable("image could not be decoded")
    engine = _get_engine()
    try:
        result, _elapsed = engine(image)
    except Exception as exc:  # noqa: BLE001
        raise OcrUnavailable(f"rapidocr inference failed: {exc}") from exc
    elements: list[Element] = []
    warnings: list[str] = []
    for item in result or []:
        try:
            box, text, score = item
        except (TypeError, ValueError):
            warnings.append(f"unexpected OCR row: {item!r}"[:160])
            continue
        text = (text or "").strip()
        if not text:
            continue
        elements.append(Element(text=text, bbox=_boxes_to_bbox(box), confidence=float(score)))
    return OcrOutput(elements=elements, engine="rapidocr-onnxruntime", warnings=warnings)
