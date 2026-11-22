"""FastAPI service exposing normalization + matching to the Go gateway (M2).

The gateway stays an I/O layer; this service owns text semantics. Endpoints are
deliberately small and side-effect free so they can be scaled independently and
tested without any database.

Run:  python -m uvicorn adguard_pipeline.api:app --port 8090
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from . import __version__
from .dictionary import load_families
from .matcher import Matcher

app = FastAPI(title="AdGuard pipeline", version=__version__)

_matcher: Matcher | None = None


def get_matcher() -> Matcher:
    global _matcher
    if _matcher is None:
        _matcher = Matcher()
    return _matcher


class TextRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=100_000, description="待检测的原始文案")
    tenant_id: str | None = Field(default=None, max_length=64, description="租户（用于日志/计费，可选）")


class DetectionOut(BaseModel):
    family: str
    canonical: str
    variant_kind: str
    span: tuple[int, int]
    evidence: str
    matched_normalized: str


class NormalizeResponse(BaseModel):
    text: str
    normalized_text: str
    compact: str
    steps: list[str]


class MatchResponse(BaseModel):
    hit: bool
    normalized_text: str
    detections: list[DetectionOut]
    matcher_version: str


@app.get("/healthz")
def healthz() -> dict[str, str]:
    matcher = get_matcher()
    return {"status": "ok", "matcher_version": matcher.version}


@app.get("/v1/version")
def version() -> dict[str, Any]:
    families = load_families()
    matcher = get_matcher()
    return {
        "pipeline_version": __version__,
        "matcher_version": matcher.version,
        "pattern_count": matcher.pattern_count,
        "families": [
            {"family": family.family, "version": family.version, "terms": len(family.terms)}
            for family in families.values()
        ],
    }


@app.post("/v1/normalize", response_model=NormalizeResponse)
def normalize(request: TextRequest) -> NormalizeResponse:
    normalized = get_matcher().normalizer.normalize(request.text)
    return NormalizeResponse(
        text=request.text,
        normalized_text=normalized.text,
        compact=normalized.compact,
        steps=list(normalized.steps),
    )


@app.post("/v1/match", response_model=MatchResponse)
def match(request: TextRequest) -> MatchResponse:
    result = get_matcher().match(request.text)
    return MatchResponse(
        hit=result.hit,
        normalized_text=result.normalized.text,
        detections=[DetectionOut(**asdict(detection)) for detection in result.detections],
        matcher_version=get_matcher().version,
    )
