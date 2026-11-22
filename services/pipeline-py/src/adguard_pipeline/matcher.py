"""Normalize + multi-pattern match, returning clause-level evidence with original spans."""

from __future__ import annotations

from dataclasses import dataclass

from .ahocorasick import AhoCorasick
from .dictionary import Family, Pattern, build_patterns, dictionary_version, load_families
from .normalization import NormalizedText, Normalizer


@dataclass(frozen=True)
class Detection:
    """A family hit with an exact original-text span (FR-8 evidence)."""

    family: str
    canonical: str
    variant_kind: str
    span: tuple[int, int]
    evidence: str
    matched_normalized: str


@dataclass(frozen=True)
class MatchResult:
    normalized: NormalizedText
    detections: tuple[Detection, ...]

    @property
    def hit(self) -> bool:
        return bool(self.detections)

    def families(self) -> set[str]:
        return {d.family for d in self.detections}


class Matcher:
    """Build once, match many. Thread-safe for read-only use."""

    def __init__(
        self,
        families: dict[str, Family] | None = None,
        normalizer: Normalizer | None = None,
    ) -> None:
        self._families = families or load_families()
        self._normalizer = normalizer or Normalizer.from_data()
        self._patterns = build_patterns(self._families, self._normalizer)

        self._text_automaton = AhoCorasick()
        self._compact_automaton = AhoCorasick()
        for pattern in self._patterns:
            if pattern.space == "text":
                self._text_automaton.add(pattern.pattern, pattern)
            else:
                self._compact_automaton.add(pattern.pattern, pattern)
        self._text_automaton.build()
        self._compact_automaton.build()

    # ------------------------------------------------------------------ metadata
    @property
    def normalizer(self) -> Normalizer:
        return self._normalizer

    @property
    def version(self) -> str:
        return f"dict:{dictionary_version(self._families)};patterns:{len(self._patterns)}"

    @property
    def pattern_count(self) -> int:
        return len(self._patterns)

    # ------------------------------------------------------------------ matching
    def normalize_only(self, text: str) -> str:
        """Expose the normalized form (used by evaluation and evidence rendering)."""
        return self._normalizer.normalize(text).text

    def match(self, text: str) -> MatchResult:
        normalized = self._normalizer.normalize(text)
        detections: list[Detection] = []

        for start, end, pattern in self._text_automaton.finditer(normalized.text):
            detection = self._to_detection(text, normalized, start, end, pattern, normalized.text[start:end])
            if detection is not None:
                detections.append(detection)

        for start, end, pattern in self._compact_automaton.finditer(normalized.compact):
            original = (
                normalized.compact_spans[start][0],
                normalized.compact_spans[end - 1][1],
            )
            if detection := self._to_detection(
                text, normalized, 0, 0, pattern, normalized.compact[start:end], original_span=original
            ):
                detections.append(detection)

        deduped = self._resolve_overlaps(detections)
        return MatchResult(normalized=normalized, detections=tuple(deduped))

    # ------------------------------------------------------------------ helpers
    def _to_detection(
        self,
        original_text: str,
        normalized: NormalizedText,
        start: int,
        end: int,
        pattern: Pattern,
        matched_normalized: str,
        original_span: tuple[int, int] | None = None,
    ) -> Detection | None:
        span = original_span or normalized.to_original_span(start, end)
        if span is None:
            return None
        return Detection(
            family=pattern.family,
            canonical=pattern.canonical,
            variant_kind=pattern.variant_kind,
            span=span,
            evidence=original_text[span[0] : span[1]],
            matched_normalized=matched_normalized.strip(),
        )

    @staticmethod
    def _resolve_overlaps(detections: list[Detection]) -> list[Detection]:
        """Keep the longest detection per family when spans are nested.

        Different families may legitimately overlap (e.g. a national-level claim that
        is also a superlative); those are all kept for M4's judgment merge.
        """
        ordered = sorted(detections, key=lambda d: (d.family, d.span[0], -(d.span[1] - d.span[0])))
        kept: list[Detection] = []
        for detection in ordered:
            start, end = detection.span
            contained = False
            for other in kept:
                if other.family != detection.family:
                    continue
                if other.span[0] <= start and end <= other.span[1]:
                    contained = True
                    break
            if not contained:
                kept.append(detection)
        return sorted(kept, key=lambda d: (d.span[0], d.span[1], d.family))
