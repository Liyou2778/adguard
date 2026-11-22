"""Family dictionary and pattern generation (ADR-1: rules are data, not code).

A *family* is a group of rule-equivalent terms (e.g. all absolute superlatives).
New regulations = new/edited JSON files; the matching code stays untouched.

Pattern kinds generated per term:
    canonical      the term itself (normalized)
    variant        explicit variants from the JSON (异体字/拆字/繁简)
    homophone      谐音 forms from the JSON
    pinyin         no-tone and tone-number pinyin forms, matched on compact text
    synonym        family-level synonym phrases (无出其右, 首屈一指, ...)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pypinyin import Style, lazy_pinyin

from .normalization import Normalizer

DEFAULT_FAMILY_DIR = Path(__file__).resolve().parents[2] / "data" / "families"


@dataclass(frozen=True)
class Term:
    canonical: str
    kind: str
    variants: tuple[str, ...]
    homophones: tuple[str, ...]


@dataclass(frozen=True)
class Family:
    family: str
    description: str
    severity: str
    law_refs: tuple[str, ...]
    terms: tuple[Term, ...]
    synonym_phrases: tuple[str, ...]
    version: int = 1

    @classmethod
    def from_dict(cls, data: dict) -> "Family":
        terms = tuple(
            Term(
                canonical=item["canonical"],
                kind=item.get("kind", "absolute"),
                variants=tuple(item.get("variants", ())),
                homophones=tuple(item.get("homophones", ())),
            )
            for item in data.get("terms", ())
        )
        return cls(
            family=data["family"],
            description=data.get("description", ""),
            severity=data.get("severity", "mid"),
            law_refs=tuple(data.get("law_refs", ())),
            terms=terms,
            synonym_phrases=tuple(data.get("synonym_phrases", ())),
            version=int(data.get("version", 1)),
        )


def load_families(directory: Path | None = None) -> dict[str, Family]:
    """Load all family JSON files from ``directory`` (sorted for determinism)."""
    path = Path(directory or DEFAULT_FAMILY_DIR)
    families: dict[str, Family] = {}
    for file in sorted(path.glob("*.json")):
        data = json.loads(file.read_text(encoding="utf-8"))
        family = Family.from_dict(data)
        if family.family in families:
            raise ValueError(f"duplicate family definition: {family.family} ({file})")
        families[family.family] = family
    if not families:
        raise FileNotFoundError(f"no family definitions under {path}")
    return families


def pinyin_forms(term: str) -> tuple[str, ...]:
    """Compact pinyin spellings used for pattern matching (no tone, tone numbers)."""
    no_tone = "".join(lazy_pinyin(term, style=Style.NORMAL))
    tone3 = "".join(lazy_pinyin(term, style=Style.TONE3, neutral_tone_with_five=True))
    forms = {no_tone, tone3}
    forms.discard("")
    return tuple(sorted(forms))


@dataclass(frozen=True)
class Pattern:
    """One automaton pattern plus the attribution needed for evidence output."""

    pattern: str
    family: str
    canonical: str
    variant_kind: str
    space: str = "text"  # "text" = normalized text, "compact" = ASCII letters/digits


def build_patterns(
    families: dict[str, Family],
    normalizer: Normalizer | None = None,
) -> list[Pattern]:
    """Expand the dictionary into deduplicated automaton patterns.

    Patterns are normalized through the same pipeline as input text, so a
    traditional/half-width spelling in the dictionary never breaks matching.
    """
    normalizer = normalizer or Normalizer.from_data()
    patterns: dict[tuple[str, str, str, str, str], Pattern] = {}

    def add(raw: str, family: str, canonical: str, kind: str, space: str) -> None:
        if not raw:
            return
        if space == "text":
            text = normalizer.normalize(raw).text
        else:
            text = normalizer.normalize(raw).compact
        text = text.strip()
        if not text:
            return
        key = (text, family, canonical, kind, space)
        if key not in patterns:
            patterns[key] = Pattern(text, family, canonical, kind, space)

    for family in families.values():
        for term in family.terms:
            add(term.canonical, family.family, term.canonical, "canonical", "text")
            for variant in term.variants:
                add(variant, family.family, term.canonical, "variant", "text")
            for homophone in term.homophones:
                add(homophone, family.family, term.canonical, "homophone", "text")
            for pinyin in pinyin_forms(term.canonical):
                add(pinyin, family.family, term.canonical, "pinyin", "compact")
        for phrase in family.synonym_phrases:
            add(phrase, family.family, phrase, "synonym", "text")

    return sorted(patterns.values(), key=lambda p: (p.space, p.family, p.pattern))


def dictionary_version(families: dict[str, Family]) -> str:
    """Stable version string for evidence/audit (rule snapshot equivalent)."""
    parts = [f"{name}:v{family.version}" for name, family in sorted(families.items())]
    return ";".join(parts)
