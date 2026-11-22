"""Embedded retrieval store: SQLite + sqlite-vec.

Why an embedded store instead of a vector server: M3 runs on a developer laptop and
an ECS with 2 vCPU / 4 GB. `sqlite-vec` gives us a real KNN vector index plus a
relational clause table in **one file**, with zero extra services. The production
path (WeKnora) uses Elasticsearch / pgvector behind the same `RetrievalStore`
contract, so swapping the backend does not touch the service or API layer.

The store also owns the **revision** column: retrieval must be able to prove that a
returned clause is still current (WeKnora's chunk-revision invariant).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import numpy as np
import sqlite_vec

from .models import ClauseRecord


class RetrievalStore:
    def __init__(self, path: Path | str = ":memory:", dim: int = 512) -> None:
        self._dim = dim
        self._lock = threading.RLock()
        database = str(path) if str(path) == ":memory:" else str(Path(path))
        if database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(database, check_same_thread=False)
        if database != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._init_schema()

    # ------------------------------------------------------------------ schema
    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS clause (
                    clause_id        TEXT PRIMARY KEY,
                    regulation_id    TEXT NOT NULL,
                    article          TEXT NOT NULL,
                    article_no       INTEGER NOT NULL,
                    chapter          TEXT,
                    level            TEXT NOT NULL,
                    item_label       TEXT,
                    parent_clause_id TEXT,
                    text             TEXT NOT NULL,
                    revision         TEXT NOT NULL,
                    law              TEXT NOT NULL DEFAULT '',
                    families         TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS vec_map (
                    rowid     INTEGER PRIMARY KEY AUTOINCREMENT,
                    clause_id TEXT UNIQUE NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS clause_vec USING vec0(embedding float[{self._dim}]);
                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    # ------------------------------------------------------------------ writes
    def upsert_clauses(self, records: list[ClauseRecord], vectors: np.ndarray) -> int:
        if len(records) != len(vectors):
            raise ValueError("records and vectors length mismatch")
        written = 0
        with self._lock:
            for record, vector in zip(records, vectors):
                self._delete_locked(record.clause_id)
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO clause
                    (clause_id, regulation_id, article, article_no, chapter, level, item_label,
                     parent_clause_id, text, revision, law, families)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.clause_id,
                        record.regulation_id,
                        record.article,
                        record.article_no,
                        record.chapter,
                        record.level,
                        record.item_label,
                        record.parent_clause_id,
                        record.text,
                        record.revision,
                        record.law,
                        json.dumps(list(record.families), ensure_ascii=False),
                    ),
                )
                cursor = self._conn.execute(
                    "INSERT INTO vec_map (clause_id) VALUES (?)", (record.clause_id,)
                )
                rowid = cursor.lastrowid
                self._conn.execute(
                    "INSERT INTO clause_vec (rowid, embedding) VALUES (?, ?)",
                    (rowid, sqlite_vec.serialize_float32(vector.astype(np.float32))),
                )
                written += 1
            self._conn.commit()
        return written

    def delete_clause(self, clause_id: str) -> bool:
        with self._lock:
            deleted = self._delete_locked(clause_id)
            self._conn.commit()
        return deleted

    def _delete_locked(self, clause_id: str) -> bool:
        row = self._conn.execute(
            "SELECT rowid FROM vec_map WHERE clause_id = ?", (clause_id,)
        ).fetchone()
        if row is None:
            return False
        self._conn.execute("DELETE FROM clause_vec WHERE rowid = ?", (row[0],))
        self._conn.execute("DELETE FROM vec_map WHERE rowid = ?", (row[0],))
        self._conn.execute("DELETE FROM clause WHERE clause_id = ?", (clause_id,))
        return True

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
            )
            self._conn.commit()

    # ------------------------------------------------------------------ reads
    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def all_clauses(self) -> list[ClauseRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT clause_id, regulation_id, article, article_no, chapter, level, item_label,"
                " parent_clause_id, text, revision, law, families FROM clause ORDER BY clause_id"
            ).fetchall()
        return [
            ClauseRecord(
                clause_id=row[0],
                regulation_id=row[1],
                article=row[2],
                article_no=row[3],
                chapter=row[4],
                level=row[5],
                item_label=row[6],
                parent_clause_id=row[7],
                text=row[8],
                revision=row[9],
                law=row[10],
                families=tuple(json.loads(row[11] or "[]")),
            )
            for row in rows
        ]

    def revisions(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute("SELECT clause_id, revision FROM clause").fetchall()
        return dict(rows)

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM clause").fetchone()[0])

    def vector_search(self, vector: np.ndarray, top_k: int = 30) -> list[tuple[str, float]]:
        """Return (clause_id, cosine distance) ordered by increasing distance."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT rowid, distance FROM clause_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (sqlite_vec.serialize_float32(vector.astype(np.float32)), top_k),
            ).fetchall()
            if not rows:
                return []
            rowids = [row[0] for row in rows]
            placeholders = ",".join("?" for _ in rowids)
            mapping = dict(
                self._conn.execute(
                    f"SELECT rowid, clause_id FROM vec_map WHERE rowid IN ({placeholders})",
                    rowids,
                ).fetchall()
            )
        return [(mapping[rowid], float(distance)) for rowid, distance in rows if rowid in mapping]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
