"""FastAPI service exposing normalization + matching + clause retrieval (M2/M3).

The gateway stays an I/O layer; this service owns text semantics. Endpoints are
deliberately small and side-effect free so they can be scaled independently and
tested without any database except the embedded retrieval store.

Run:  python -m uvicorn adguard_pipeline.api:app --port 8090
"""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from . import __version__
from .dictionary import load_families
from .matcher import Matcher
from .retrieval.clause_source import ControlPlaneClauseSource, JsonClauseSource
from .retrieval.models import ClauseRecord
from .retrieval.service import RetrievalService
from .retrieval.store import RetrievalStore

app = FastAPI(title="AdGuard pipeline", version=__version__)

_matcher: Matcher | None = None
_retrieval: RetrievalService | None = None
_retrieval_store: RetrievalStore | None = None
DEFAULT_RETRIEVAL_DB = Path(__file__).resolve().parents[4] / ".tmp" / "retrieval" / "index.sqlite3"


def get_matcher() -> Matcher:
    global _matcher
    if _matcher is None:
        _matcher = Matcher()
    return _matcher


def _clause_source() -> Any:
    kind = os.environ.get("ADGUARD_CLAUSE_SOURCE", "json").lower()
    if kind == "control-plane":
        base_url = os.environ.get("ADGUARD_CONTROL_PLANE_URL", "http://127.0.0.1:8081")
        return ControlPlaneClauseSource(base_url)
    return JsonClauseSource(_parsed_path())


def _parsed_path() -> Path:
    override = os.environ.get("ADGUARD_PARSED_CLAUSES", "")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data" / "parsers" / "parsed_m2.json"


def get_retrieval() -> RetrievalService:
    global _retrieval, _retrieval_store
    if _retrieval is not None:
        return _retrieval
    embedder = _resolve_embedder()
    db_env = os.environ.get("ADGUARD_RETRIEVAL_DB", "")
    db_path = Path(db_env) if db_env else DEFAULT_RETRIEVAL_DB
    store = RetrievalStore(db_path, dim=embedder.dim)
    service = RetrievalService(store, embedder=embedder, matcher=get_matcher())
    records = _clause_source().load()
    service.build(records)
    _retrieval = service
    _retrieval_store = store
    return service


def _resolve_embedder():
    from .retrieval.embeddings import resolve_embedder

    preference = os.environ.get("ADGUARD_EMBEDDER", "auto")
    model_dir = os.environ.get("ADGUARD_EMBED_MODEL_DIR", "")
    return resolve_embedder(preference, Path(model_dir) if model_dir else None)


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


# --------------------------------------------------------------------------- M3 retrieval
class RetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4_000, description="素材文案 / 查询文本")
    top_k: int = Field(default=5, ge=1, le=20)
    use_dense: bool = True
    use_rerank: bool = True
    tenant_id: str | None = Field(default=None, max_length=64)


class RetrievalHitOut(BaseModel):
    clause_id: str
    revision: str
    score: float
    bm25_score: float
    dense_score: float
    rerank_score: float
    family_hits: int
    text: str
    article: str
    item_label: str | None = None
    regulation_id: str
    law: str = ""


class RetrieveResponse(BaseModel):
    query: str
    index_version: str
    dropped_stale: int
    warnings: list[str]
    hits: list[RetrievalHitOut]
    embedder: str


@app.post("/v1/retrieve", response_model=RetrieveResponse)
def retrieve(request: RetrieveRequest) -> RetrieveResponse:
    service = get_retrieval()
    result = service.retrieve(
        request.query,
        top_k=request.top_k,
        use_dense=request.use_dense,
        use_rerank=request.use_rerank,
    )
    return RetrieveResponse(
        query=result.query,
        index_version=result.index_version,
        dropped_stale=result.dropped_stale,
        warnings=result.warnings,
        hits=[RetrievalHitOut(**asdict(hit)) for hit in result.hits],
        embedder=service.embedder.name,
    )


@app.get("/v1/index/status")
def index_status() -> dict[str, Any]:
    status = get_retrieval().status()
    return {
        "clauses": status.clauses,
        "index_version": status.index_version,
        "embedder": status.embedder,
        "dimensions": status.dimensions,
        "updated_at": status.updated_at,
        "source": os.environ.get("ADGUARD_CLAUSE_SOURCE", "json"),
    }


@app.post("/v1/index/rebuild")
def rebuild_index() -> dict[str, Any]:
    service = get_retrieval()
    status = service.rebuild(_clause_source())
    return {"status": "rebuilt", "clauses": status.clauses,
            "index_version": status.index_version, "embedder": status.embedder}

# --------------------------------------------------------------------------- M3 judge
_rule_judge = None


def _model_judge_client():
    """Optional M4 judge model served by vLLM/SGLang (M5 hybrid path).

    Disabled by default; set ADGUARD_MODEL_JUDGE_URL=http://host:8100 to enable.
    """
    url = os.environ.get("ADGUARD_MODEL_JUDGE_URL", "").strip()
    if not url:
        return None
    from .retrieval.model_judge import ModelJudgeClient

    return ModelJudgeClient(
        url,
        model=os.environ.get("ADGUARD_MODEL_JUDGE_MODEL", "adguard-judge"),
        timeout=float(os.environ.get("ADGUARD_MODEL_JUDGE_TIMEOUT", "8")),
        max_tokens=int(os.environ.get("ADGUARD_MODEL_JUDGE_MAX_TOKENS", "384")),
    )


def get_rule_judge():
    global _rule_judge
    if _rule_judge is None:
        from .retrieval.rule_judge import RuleJudge

        _rule_judge = RuleJudge(get_retrieval(), get_matcher(), model_client=_model_judge_client())
    return _rule_judge


class JudgeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=200_000)
    tenant_id: str | None = Field(default=None, max_length=64)


class JudgeResponse(BaseModel):
    decision: str
    reason: str
    violations: list[dict[str, Any]]
    judge_version: str
    retrieval_warnings: list[str]
    dropped_stale: int
    index_version: str


@app.post("/v1/judge", response_model=JudgeResponse)
def judge(request: JudgeRequest) -> JudgeResponse:
    judgment = get_rule_judge().judge(request.text)
    return JudgeResponse(**judgment.to_dict())