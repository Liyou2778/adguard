"""Lightweight layout analysis: classify OCR elements into title / body / corner and rebuild
reading-order lines. Deliberately heuristic (no heavy layout model) so it runs on a 2C4G ECS.

The PRD (FR-2) requires 版面分析识别标题/正文/角标 and coordinates must be preserved. This module
keeps every element's original bbox and only adds a role, so downstream evidence can still point
at the original pixel region.
"""

from __future__ import annotations

from statistics import median

from .types import BBox, Element, Line


def _height(e: Element) -> float:
    return max(1.0, e.bbox[3] - e.bbox[1])


def _width(e: Element) -> float:
    return max(1.0, e.bbox[2] - e.bbox[0])


def classify(elements: list[Element], image_w: float, image_h: float) -> tuple[list[Element], list[str]]:
    warnings: list[str] = []
    if not elements:
        return [], ["no OCR elements to classify"]
    if image_w <= 0 or image_h <= 0:
        warnings.append("image size unknown; layout uses relative fallbacks")
        image_w = image_w or 1000.0
        image_h = image_h or 1000.0
    med_h = median(_height(e) for e in elements)
    for e in elements:
        h = _height(e)
        at_left_edge = e.bbox[0] <= 0.08 * image_w
        at_right_edge = e.bbox[2] >= 0.92 * image_w
        near_top = e.bbox[1] <= 0.25 * image_h
        if near_top and (h >= 1.3 * med_h or len(e.text) <= 12):
            e.role = "title"
        elif (at_left_edge or at_right_edge) and h <= 1.15 * med_h:
            e.role = "corner"
        else:
            e.role = "body"
    return elements, warnings


def _row_group(elements: list[Element]) -> list[list[Element]]:
    ordered = sorted(elements, key=lambda e: (e.bbox[1], e.bbox[0]))
    rows: list[list[Element]] = []
    for e in ordered:
        placed = False
        for row in rows:
            ref = row[-1]
            overlap = min(ref.bbox[3], e.bbox[3]) - max(ref.bbox[1], e.bbox[1])
            if overlap >= 0.5 * min(_height(ref), _height(e)):
                row.append(e)
                placed = True
                break
        if not placed:
            rows.append([e])
    return rows


def build_lines(elements: list[Element]) -> list[Line]:
    lines: list[Line] = []
    for row in _row_group(elements):
        row.sort(key=lambda e: e.bbox[0])
        text = " ".join(e.text.strip() for e in row if e.text.strip())
        if not text:
            continue
        bbox: BBox = [
            min(e.bbox[0] for e in row),
            min(e.bbox[1] for e in row),
            max(e.bbox[2] for e in row),
            max(e.bbox[3] for e in row),
        ]
        lines.append(Line(text=text, role=row[0].role, bbox=bbox, frame=row[0].frame))
    return lines


def analyze(elements: list[Element], image_w: float, image_h: float) -> tuple[list[Element], list[Line], dict[str, int], list[str]]:
    classified, warnings = classify(elements, image_w, image_h)
    lines = build_lines(classified)
    counts: dict[str, int] = {"title": 0, "body": 0, "corner": 0}
    for e in classified:
        counts[e.role] = counts.get(e.role, 0) + 1
    return classified, lines, counts, warnings
