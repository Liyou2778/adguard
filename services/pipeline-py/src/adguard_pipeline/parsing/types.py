"""Types shared by the parsing (multimodal / ingestion) subsystem."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


BBox = list[float]  # [x0, y0, x1, y1]


@dataclass
class ScanResult:
    """Antivirus verdict. `status` is clean | infected | unknown."""

    status: str
    engine: str
    signature: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Element:
    """One OCR/layout element with its original coordinates."""

    text: str
    bbox: BBox
    confidence: float
    role: str = "body"  # title | body | corner
    frame: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Line:
    text: str
    role: str
    bbox: BBox
    frame: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParsedAsset:
    asset_type: str
    text: str
    elements: list[Element] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    role_counts: dict[str, int] = field(default_factory=dict)
    frames: int = 0
    degraded: bool = False
    warnings: list[str] = field(default_factory=list)
    scan: ScanResult | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_type": self.asset_type,
            "text": self.text,
            "elements": [e.to_dict() for e in self.elements],
            "lines": [l.to_dict() for l in self.lines],
            "role_counts": self.role_counts,
            "frames": self.frames,
            "degraded": self.degraded,
            "warnings": self.warnings,
            "scan": self.scan.to_dict() if self.scan else None,
            "meta": self.meta,
        }
