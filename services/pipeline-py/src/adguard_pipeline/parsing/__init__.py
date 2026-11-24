"""Parsing subsystem (FR-1 ingestion extras + FR-2 multimodal parsing).

Public entry points used by the API layer:

- parse_bytes(asset_type, data, mime, filename): text|image|video
- parse_url(url): landing_page
- scan_bytes(data, filename): antivirus verdict (never raises)
"""

from __future__ import annotations

from .media import parse_image, parse_text, parse_video
from .scan import scan_bytes, scan_required
from .types import Element, Line, ParsedAsset, ScanResult
from .web import UnsafeURLError, fetch_url, guard_url

__all__ = [
    "Element",
    "Line",
    "ParsedAsset",
    "ScanResult",
    "UnsafeURLError",
    "fetch_url",
    "guard_url",
    "parse_bytes",
    "parse_url",
    "scan_bytes",
    "scan_required",
]


def parse_bytes(asset_type: str, data: bytes, mime: str = "", filename: str = "") -> ParsedAsset:
    kind = (asset_type or "text").lower()
    if kind == "image":
        return parse_image(data, mime=mime, filename=filename)
    if kind == "video":
        return parse_video(data, mime=mime, filename=filename)
    if kind == "text":
        return parse_text(data, mime=mime, filename=filename)
    raise ValueError(f"unsupported asset_type {asset_type!r} for byte parsing (use parse_url for landing_page)")


def parse_url(url: str) -> ParsedAsset:
    return fetch_url(url)
