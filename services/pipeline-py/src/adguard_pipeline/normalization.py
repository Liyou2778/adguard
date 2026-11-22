"""Text normalization pipeline with original-offset preservation.

Why a dedicated module: normalization is the only place where we can *prove*
(resp. measure) that character-level evasion is being defeated. Every transform
keeps a mapping from normalized character -> original character range, so a match
in normalized space can always be rendered as an original-text span (FR-8).

Pipeline:
    NFKC/NFKD + strip zero-width/control/emoji
      -> traditional -> simplified (char by char, 1:1)
      -> variant chars (异体字/同形字)
      -> component sequences (拆字, e.g. 曰取 -> 最)
      -> lowercase ASCII (pinyin in text stays as letters; matching happens on `compact`)

Pinyin restoration is deliberately *not* a text rewrite: rewriting is lossy and
ambiguous. Instead the matcher matches pinyin patterns against ``compact``
(ASCII letters/digits only), which keeps offsets exact.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

try:  # optional at import time so tests can run without the data file
    from opencc import OpenCC
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    OpenCC = None  # type: ignore[assignment]

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# Zero-width / bidi-control / soft-hyphen style characters used by evasion.
_ZERO_WIDTH = {
    "\u200b", "\u200c", "\u200d", "\u200e", "\u200f",
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
    "\u2060", "\u2061", "\u2062", "\u2063", "\u2064",
    "\ufeff", "\u00ad", "\u180e", "\u2066", "\u2067", "\u2068", "\u2069",
}

_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),  # emoji / pictographs
    (0x2600, 0x27BF),    # misc symbols, dingbats
    (0x2B00, 0x2BFF),    # arrows/stars
    (0xFE00, 0xFE0F),    # variation selectors
    (0x1F1E6, 0x1F1FF),  # flags
)


@dataclass(frozen=True)
class NormalizedText:
    """Normalized text plus the mapping back to the original string.

    ``spans[i]`` is the original ``[start, end)`` range that produced normalized
    character ``i``. Component sequences (2+ original chars -> 1 char) produce a
    merged range; stripped characters produce no entry (they are covered by the
    span of the surrounding characters when a match is expanded).
    """

    text: str
    spans: tuple[tuple[int, int], ...]
    compact: str
    compact_spans: tuple[tuple[int, int], ...]
    steps: tuple[str, ...]

    def to_original_span(self, start: int, end: int) -> tuple[int, int] | None:
        """Map a normalized ``[start, end)`` range back to the original text."""
        if start < 0 or end > len(self.spans) or start >= end:
            return None
        return self.spans[start][0], self.spans[end - 1][1]


def _should_strip(ch: str) -> bool:
    if ch in _ZERO_WIDTH:
        return True
    category = unicodedata.category(ch)
    if category in ("Cf", "Cc", "Cs", "Co", "So", "Sk"):
        return True
    code = ord(ch)
    for lo, hi in _EMOJI_RANGES:
        if lo <= code <= hi:
            return True
    return False


def _normalize_char(ch: str) -> str:
    """NFKC + NFKD-with-combining-marks-removed, so pinyin tone marks fold away."""
    ch = unicodedata.normalize("NFKC", ch)
    kept = [
        part
        for part in unicodedata.normalize("NFKD", ch)
        if unicodedata.category(part) != "Mn"  # drop combining marks (tone marks, variation)
    ]
    return "".join(kept)


class Normalizer:
    """Configurable normalizer; the char maps live in data/ (ADR-1: rules as data)."""

    def __init__(
        self,
        variant_chars: dict[str, str] | None = None,
        component_sequences: dict[str, str] | None = None,
    ) -> None:
        self._variant_chars = dict(variant_chars or {})
        self._component_sequences = dict(component_sequences or {})
        self._t2s = OpenCC("t2s") if OpenCC is not None else None
        self._t2s_cache: dict[str, str] = {}
        # Longest component sequence first (4,3,2) so 曰取自取 style garbage does not
        # shadow a longer known split form.
        self._component_max = max((len(k) for k in self._component_sequences), default=0)

    @classmethod
    def from_data(cls, path: Path | None = None) -> "Normalizer":
        data_path = path or (DEFAULT_DATA_DIR / "normalization" / "char_map.json")
        raw = json.loads(Path(data_path).read_text(encoding="utf-8"))
        return cls(
            variant_chars=raw.get("variant_chars", {}),
            component_sequences=raw.get("component_sequences", {}),
        )

    # ------------------------------------------------------------------ pipeline
    def normalize(self, raw: str) -> NormalizedText:
        chars: list[str] = []
        spans: list[tuple[int, int]] = []

        for index, original in enumerate(raw):
            for char in _normalize_char(original):
                if _should_strip(char):
                    continue
                char = self._to_simplified(char)
                char = self._variant_chars.get(char, char)
                if char.isascii() and char.isupper():
                    char = char.lower()
                if char == "":
                    continue
                if char.isspace():
                    # Normalize all whitespace to a plain space; keep it so pinyin
                    # phrases like "zui hao" remain tokenizable.
                    char = " "
                chars.append(char)
                spans.append((index, index + 1))

        chars, spans = self._apply_component_sequences(chars, spans)

        text = "".join(chars)
        compact_chars: list[str] = []
        compact_spans: list[tuple[int, int]] = []
        for char, span in zip(chars, spans):
            if char.isascii() and char.isalnum():
                compact_chars.append(char)
                compact_spans.append(span)

        return NormalizedText(
            text=text,
            spans=tuple(spans),
            compact="".join(compact_chars),
            compact_spans=tuple(compact_spans),
            steps=(
                "nfkc+nfkd",
                "strip-zero-width/control/emoji",
                "t2s",
                "variant-chars",
                "component-sequences",
                "lowercase",
            ),
        )

    # ------------------------------------------------------------------ helpers
    def _to_simplified(self, char: str) -> str:
        if self._t2s is None:
            return char
        cached = self._t2s_cache.get(char)
        if cached is not None:
            return cached
        converted = self._t2s.convert(char)
        # Only accept 1:1 conversions; phrase-level conversions can change length and
        # would silently break the offset map.
        result = converted if len(converted) == 1 else char
        self._t2s_cache[char] = result
        return result

    def _apply_component_sequences(
        self,
        chars: list[str],
        spans: list[tuple[int, int]],
    ) -> tuple[list[str], list[tuple[int, int]]]:
        if not self._component_sequences:
            return chars, spans
        out_chars: list[str] = []
        out_spans: list[tuple[int, int]] = []
        i = 0
        while i < len(chars):
            matched = False
            for length in range(min(self._component_max, len(chars) - i), 1, -1):
                sequence = "".join(chars[i : i + length])
                replacement = self._component_sequences.get(sequence)
                if replacement is not None:
                    out_chars.append(replacement)
                    out_spans.append((spans[i][0], spans[i + length - 1][1]))
                    i += length
                    matched = True
                    break
            if not matched:
                out_chars.append(chars[i])
                out_spans.append(spans[i])
                i += 1
        return out_chars, out_spans
