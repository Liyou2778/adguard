"""Generate the M2 variant evaluation set (deterministic, reproducible).

Output: data/datasets/m2_variants.jsonl with three kinds of samples:
    variant   known attack families (the 7 families we promise to cover)
    negative  compliant text without any violation term (false-positive check)
    unknown   out-of-scope paraphrases / unseen homophones (honest coverage gap;
              expected to be handled by the M4 semantic model, not by M2 rules)

Each sample records the planted span in the original text so evaluation can check
that the *right region* was detected, not merely that some hit existed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from .dictionary import load_families
from .variants import (
    NEGATIVE_TEMPLATES,
    SEMANTIC_PARAPHRASES,
    TEMPLATES,
    UNKNOWN_HOMOPHONE_CHARS,
    PlantedSample,
    _plant,
    emoji_forms,
    fullwidth_forms,
    homophone_forms,
    pinyin_variants,
    split_char_forms,
    variant_char_forms,
    zero_width_forms,
)

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "data" / "datasets" / "m2_variants.jsonl"
SEED = 20261122


@dataclass
class Sample:
    id: str
    kind: str  # variant | negative | unknown
    variant_family: str
    expected_family: str
    term: str
    text: str
    span_start: int
    span_end: int
    template_id: int


def _planted(text: str, span: tuple[int, int], term: str, kind: str, variant_family: str,
             template_id: int, index: int) -> Sample:
    return Sample(
        id=f"{kind}-{variant_family}-{index:05d}",
        kind=kind,
        variant_family=variant_family,
        expected_family="absolute_terms",
        term=term,
        text=text,
        span_start=span[0],
        span_end=span[1],
        template_id=template_id,
    )


def _plant_sample(template: str, term: str) -> PlantedSample:
    return _plant(template, term)


def generate(seed: int = SEED) -> list[Sample]:
    rng = random.Random(seed)
    families = load_families()
    family = families["absolute_terms"]
    terms = family.terms
    samples: list[Sample] = []
    counter = 0

    def add(kind: str, variant_family: str, term: str, form: str, template_id: int) -> None:
        nonlocal counter
        planted = _plant_sample(TEMPLATES[template_id], form)
        samples.append(
            _planted(planted.text, planted.span, term, kind, variant_family, template_id, counter)
        )
        counter += 1

    # ---- 1. canonical baseline (proves the pipeline does not break normal text)
    for term in terms:
        for template_id in (0, 3):
            add("variant", "canonical", term.canonical, term.canonical, template_id)

    # ---- 2. pinyin (4 spellings x 3 templates)
    for term in terms:
        forms = pinyin_variants(term.canonical, rng)
        for form_index, form in enumerate(forms[:4]):
            for template_id in (0, 1, 3):
                add("variant", "pinyin", term.canonical, form, (template_id + form_index) % len(TEMPLATES))

    # ---- 3. emoji separators (2 forms x 3 templates)
    for term in terms:
        forms = emoji_forms(term.canonical, rng)
        for form_index, form in enumerate(forms):
            for template_id in (1, 4, 6):
                add("variant", "emoji", term.canonical, form, (template_id + form_index) % len(TEMPLATES))

    # ---- 4. zero-width / control characters (3 positions x 3 templates)
    for term in terms:
        forms = zero_width_forms(term.canonical, rng) + zero_width_forms(term.canonical, rng)
        for form_index, form in enumerate(forms[:3]):
            for template_id in (0, 2, 5):
                add("variant", "zero_width", term.canonical, form, (template_id + form_index) % len(TEMPLATES))

    # ---- 5. homophones (dictionary-listed, i.e. known family coverage)
    for term in terms:
        forms = homophone_forms(term.canonical, term.homophones)
        known = [form for form in forms if form in term.homophones]
        for form_index, form in enumerate(known[:2]):
            for template_id in (0, 2, 5):
                add("variant", "homophone", term.canonical, form, (template_id + form_index) % len(TEMPLATES))

    # ---- 6. split characters (拆字) & variant characters (异体/繁体)
    for term in terms:
        for form in split_char_forms(term.canonical):
            for template_id in (1, 3, 4):
                add("variant", "split_char", term.canonical, form, template_id)
        for form in variant_char_forms(term.canonical) + fullwidth_forms(term.canonical):
            for template_id in (0, 2, 6):
                add("variant", "variant_char", term.canonical, form, template_id)

    # ---- 7. synonym phrases / classical wording (family-level dictionary)
    for phrase in family.synonym_phrases:
        for template_id in range(len(TEMPLATES)):
            add("variant", "synonym", phrase, phrase, template_id)

    # ---- negatives: compliant copy without violations
    # Note: the prefix deliberately avoids "第N" -- the dictionary contains "第1" as a
    # variant of "第一", and an ordinal like "第1期" is a real lexical false positive
    # that M2 cannot resolve without context (tracked for M3/M4 context handling).
    for index in range(200):
        template = NEGATIVE_TEMPLATES[index % len(NEGATIVE_TEMPLATES)]
        text = f"方案{index + 1:03d}：{template}"
        samples.append(
            Sample(
                id=f"negative-{index:05d}",
                kind="negative",
                variant_family="negative",
                expected_family="",
                term="",
                text=text,
                span_start=0,
                span_end=0,
                template_id=index % len(NEGATIVE_TEMPLATES),
            )
        )

    # ---- unknown set: unseen homophones (same syllable, uncommon char) + semantics
    unknown_index = 0
    for term in terms:
        for char_index, char in enumerate(term.canonical):
            for replacement in UNKNOWN_HOMOPHONE_CHARS.get(char, ()):
                form = term.canonical[:char_index] + replacement + term.canonical[char_index + 1 :]
                template_id = unknown_index % len(TEMPLATES)
                planted = _plant_sample(TEMPLATES[template_id], form)
                samples.append(
                    _planted(planted.text, planted.span, term.canonical, "unknown",
                             "unseen_homophone", template_id, unknown_index)
                )
                unknown_index += 1
    for phrase in SEMANTIC_PARAPHRASES:
        for template_id in range(len(TEMPLATES)):
            planted = _plant_sample(TEMPLATES[template_id], phrase)
            samples.append(
                _planted(planted.text, planted.span, phrase, "unknown",
                         "semantic_paraphrase", template_id, unknown_index)
            )
            unknown_index += 1

    return samples


def write_dataset(samples: list[Sample], path: Path = DEFAULT_OUT) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(asdict(sample), ensure_ascii=False) for sample in samples) + "\n"
    path.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest


def stats(samples: list[Sample]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for sample in samples:
        bucket = result.setdefault(sample.kind, {})
        bucket[sample.variant_family] = bucket.get(sample.variant_family, 0) + 1
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the M2 variant evaluation set")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    samples = generate(args.seed)
    digest = write_dataset(samples, args.out)
    breakdown = stats(samples)
    total_variant = sum(count for count in breakdown.get("variant", {}).values())
    print(f"dataset : {args.out}")
    print(f"sha256  : {digest}")
    print(f"total   : {len(samples)} samples")
    print(f"variant : {total_variant} (gate: >= 1000)")
    for kind, counts in breakdown.items():
        print(f"  {kind:8s} " + ", ".join(f"{name}={n}" for name, n in sorted(counts.items())))


if __name__ == "__main__":
    main()
