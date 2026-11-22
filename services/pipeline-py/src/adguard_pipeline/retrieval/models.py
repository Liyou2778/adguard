"""Retrieval data model for clause-level rule search (M3).

A clause record carries three things the retriever must never lose:

* **identity** (`clause_id`) - what the judgment cites;
* **revision** (`revision`) - content hash, used to drop stale hits after an update
  (the WeKnora `TestRerankEnrichmentDropsChangedChunkRevision` invariant);
* **text** - article-level or item-level statute text, already normalized upstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClauseRecord:
    clause_id: str
    regulation_id: str
    article: str
    article_no: int
    chapter: str | None
    level: str  # article | item
    item_label: str | None
    parent_clause_id: str | None
    text: str
    revision: str  # sha256 of the clause text (content hash)
    law: str = ""
    families: tuple[str, ...] = ()

    def display(self) -> str:
        item = self.item_label or ""
        return f"{self.article}{item}"


@dataclass(frozen=True)
class RetrievalHit:
    clause_id: str
    revision: str
    score: float
    bm25_score: float
    dense_score: float
    rerank_score: float
    family_hits: int
    text: str
    article: str
    item_label: str | None
    regulation_id: str
    law: str = ""
    families: tuple[str, ...] = ()


@dataclass
class RetrievalResult:
    query: str
    hits: list[RetrievalHit]
    dropped_stale: int = 0
    index_version: str = ""
    warnings: list[str] = field(default_factory=list)
