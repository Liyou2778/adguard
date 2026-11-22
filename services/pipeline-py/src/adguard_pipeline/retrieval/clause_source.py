"""Clause sources: where the retriever gets its documents from.

Two adapters keep the retriever testable and production-shaped:
    JsonClauseSource          - the M2 parser output (offline build / CI / eval)
    ControlPlaneClauseSource  - the Java control plane DB via its admin API (M3 runtime)
Both expose the same `load()` contract and a content hash as `revision`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

import httpx

from .models import ClauseRecord

DEFAULT_PARSED = Path(__file__).resolve().parents[3] / "data" / "parsers" / "parsed_m2.json"


def text_revision(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ClauseSource(Protocol):
    def load(self) -> list[ClauseRecord]:
        ...


class JsonClauseSource:
    """Reads the clause tree embedded in the M2 parser output."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path or DEFAULT_PARSED)

    def load(self) -> list[ClauseRecord]:
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        records: list[ClauseRecord] = []
        for regulation in payload["regulations"]:
            law = regulation.get("title", "")
            for clause in regulation.get("clauses", []):
                article_no = int(clause["article_no"])
                clause_id = clause["clause_id"]
                records.append(
                    ClauseRecord(
                        clause_id=clause_id,
                        regulation_id=regulation["id"],
                        article=clause["article"],
                        article_no=article_no,
                        chapter=clause.get("chapter"),
                        level="article",
                        item_label=None,
                        parent_clause_id=None,
                        text=clause["text"],
                        revision=text_revision(clause["text"]),
                        law=law,
                    )
                )
                for item in clause.get("items", []):
                    item_clause_id = f"{clause_id}-ITEM{item['index']}"
                    records.append(
                        ClauseRecord(
                            clause_id=item_clause_id,
                            regulation_id=regulation["id"],
                            article=clause["article"],
                            article_no=article_no,
                            chapter=clause.get("chapter"),
                            level="item",
                            item_label=item["item"],
                            parent_clause_id=clause_id,
                            text=item["text"],
                            revision=text_revision(item["text"]),
                            law=law,
                        )
                    )
        return records


class ControlPlaneClauseSource:
    """Reads clauses from the Java control plane (source of truth in M3 runtime)."""

    def __init__(self, base_url: str = "http://127.0.0.1:8081", timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def load(self) -> list[ClauseRecord]:
        records: list[ClauseRecord] = []
        with httpx.Client(timeout=self._timeout) as client:
            regulations = client.get(f"{self._base_url}/admin/regulations").json()
            titles = {entry["id"]: entry.get("title", "") for entry in regulations}
            for regulation_id, law in titles.items():
                clauses = client.get(
                    f"{self._base_url}/admin/regulations/{regulation_id}/clauses"
                ).json()
                for clause in clauses:
                    records.append(
                        ClauseRecord(
                            clause_id=clause["clause_id"],
                            regulation_id=clause["regulation_id"],
                            article=clause["article"],
                            article_no=int(clause["article_no"]),
                            chapter=clause.get("chapter"),
                            level=clause["level"],
                            item_label=clause.get("item_label"),
                            parent_clause_id=clause.get("parent_clause_id"),
                            text=clause["text"],
                            # the control plane stores the sha256 of the imported text
                            revision=clause["checksum"],
                            law=law,
                        )
                    )
        return records


class InMemoryClauseSource:
    def __init__(self, records: list[ClauseRecord]) -> None:
        self._records = list(records)

    def load(self) -> list[ClauseRecord]:
        return list(self._records)
