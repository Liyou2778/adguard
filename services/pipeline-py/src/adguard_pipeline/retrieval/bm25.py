"""Character n-gram BM25 for Chinese clause retrieval (no external search engine).

Why hand-rolled: 33 clauses and a few hundred eval queries; an embedded SQLite FTS5
trigram index would work, but a pure-python implementation is transparent, has no
tokenizer dependency, and lets us pin k1/b parameters in the report.

Tokenization: normalize (M2 pipeline, so variant evasion also helps retrieval),
keep CJK characters and ASCII alphanumerics, emit character bigrams (plus unigrams
for one-character leftovers). Chinese has no spaces, so bigram BM25 is the standard
cheap sparse baseline.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ..normalization import Normalizer

_TOKEN_KEEP = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for chunk in _TOKEN_KEEP.findall(text):
        if len(chunk) == 1:
            tokens.append(chunk)
            continue
        tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    return tokens


@dataclass
class CharBigramBM25:
    k1: float = 1.2
    b: float = 0.75

    def __post_init__(self) -> None:
        self._docs: dict[str, list[str]] = {}
        self._doc_len: dict[str, int] = {}
        self._tf: dict[str, dict[str, int]] = {}
        self._df: dict[str, int] = {}
        self._avgdl: float = 0.0
        self._normalizer = Normalizer.from_data()

    def fit(self, documents: dict[str, str]) -> "CharBigramBM25":
        self._docs = {}
        self._doc_len = {}
        self._tf = {}
        self._df = {}
        for doc_id, text in documents.items():
            normalized = self._normalizer.normalize(text).text
            tokens = tokenize(normalized)
            self._docs[doc_id] = tokens
            self._doc_len[doc_id] = len(tokens)
            term_freq: dict[str, int] = {}
            for token in tokens:
                term_freq[token] = term_freq.get(token, 0) + 1
            self._tf[doc_id] = term_freq
            for token in set(term_freq):
                self._df[token] = self._df.get(token, 0) + 1
        self._avgdl = (
            sum(self._doc_len.values()) / len(self._doc_len) if self._doc_len else 0.0
        )
        return self

    def _idf(self, token: str) -> float:
        n = len(self._doc_len)
        df = self._df.get(token, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def score(self, query: str, doc_id: str) -> float:
        if doc_id not in self._tf:
            return 0.0
        tokens = tokenize(self._normalizer.normalize(query).text)
        if not tokens:
            return 0.0
        doc_len = self._doc_len[doc_id]
        term_freq = self._tf[doc_id]
        score = 0.0
        for token in tokens:
            freq = term_freq.get(token, 0)
            if freq == 0:
                continue
            denominator = freq + self.k1 * (1 - self.b + self.b * doc_len / (self._avgdl or 1))
            score += self._idf(token) * freq * (self.k1 + 1) / denominator
        return score

    def search(self, query: str, top_k: int = 30) -> list[tuple[str, float]]:
        scored = [(doc_id, self.score(query, doc_id)) for doc_id in self._docs]
        scored.sort(key=lambda item: (-item[1], item[0]))
        return [(doc_id, score) for doc_id, score in scored[:top_k] if score > 0]

    @property
    def size(self) -> int:
        return len(self._docs)
