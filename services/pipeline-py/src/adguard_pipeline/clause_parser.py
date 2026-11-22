"""Regulation clause parser: legal text -> clause tree -> structured rules (data).

Why this exists: PRD ADR-1 requires rules to be data. Somebody has to turn the
statute into that data, and doing it by hand does not scale when a new regulation
lands. The parser is deliberately conservative:

    * segmentation by 第X条 / （X） / 第X章 anchors only (no NLP guessing);
    * amount/date/term extraction by explicit regexes, validated against a gold set;
    * anything it cannot map stays as a clause (no fabricated rules).

Output JSON is consumed by the Java control plane (rule library) and, later, by the
M3 clause-level retrieval index.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .dictionary import load_families

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_MAP = DEFAULT_DATA_DIR / "parsers" / "family_map.json"

ARTICLE_RE = re.compile(r"第([零〇一二三四五六七八九十百千两\d]+)条")
ITEM_RE = re.compile(r"（([一二三四五六七八九十]+)）")
CHAPTER_RE = re.compile(r"第[零〇一二三四五六七八九十百千两\d]+章\s*\S+")
GUIDELINE_SECTION_RE = re.compile(r"^([一二三四五六七八九十]+)、", re.MULTILINE)
QUOTED_RE = re.compile(r"[“\"]([^”\"]{1,20})[”\"]")
AMOUNT_RANGE_RE = re.compile(
    r"([零〇一二三四五六七八九十百千万亿两\d]+)元以上([零〇一二三四五六七八九十百千万亿两\d]+)元以下"
)
EFFECTIVE_RE = re.compile(r"自(\d{4})年(\d{1,2})月(\d{1,2})日起施行")

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def chinese_number_to_int(text: str) -> int:
    """Convert ``第九``/``二十八``/``一百零五`` style numerals (also plain digits)."""
    text = text.strip()
    if not text:
        raise ValueError("empty numeral")
    if text.isdigit():
        return int(text)
    total = 0
    section = 0
    number = 0
    for char in text:
        if char in _CN_DIGITS:
            number = _CN_DIGITS[char]
        elif char in _CN_UNITS:
            if number == 0:
                number = 1
            section += number * _CN_UNITS[char]
            number = 0
        elif char == "万":
            section = (section + number) * 10_000
            total += section
            section = 0
            number = 0
        elif char == "亿":
            total = (total + section + number) * 100_000_000
            section = 0
            number = 0
        else:
            raise ValueError(f"unsupported numeral character: {char!r} in {text!r}")
    return total + section + number


@dataclass
class Item:
    item: str  # e.g. （三）
    index: int
    text: str
    char_start: int
    char_end: int


@dataclass
class Clause:
    clause_id: str
    article: str
    article_no: int
    chapter: str | None
    text: str
    char_start: int
    char_end: int
    items: list[Item] = field(default_factory=list)


@dataclass
class RegulationDoc:
    regulation_id: str
    title: str
    source_url: str
    effective_from: str | None
    text: str
    clauses: list[Clause] = field(default_factory=list)

    def clause(self, article_no: int) -> Clause | None:
        for clause in self.clauses:
            if clause.article_no == article_no:
                return clause
        return None


@dataclass
class ParsedRule:
    rule_id: str
    family: str
    severity: str
    regulation_id: str
    article: str
    item: str | None
    clause_id: str
    text: str
    canonical_terms: list[str]
    penalty_law_ref: str | None
    penalty_min: int | None
    penalty_max: int | None
    effective_from: str | None
    context_exemptions: list[str]
    source_url: str
    law: str


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class ClauseParser:
    def __init__(self, family_map_path: Path | None = None) -> None:
        raw = json.loads(Path(family_map_path or DEFAULT_MAP).read_text(encoding="utf-8"))
        self._regulations: dict[str, dict] = raw["regulations"]
        self._rule_specs: list[dict] = raw["rules"]
        self._families = load_families()
        self.data_dir = Path(family_map_path or DEFAULT_MAP).parent.parent

    # ------------------------------------------------------------------ load
    def load(self, regulation_id: str) -> RegulationDoc:
        meta = self._regulations[regulation_id]
        path = self.data_dir / "regulations" / meta["file"]
        text = path.read_text(encoding="utf-8")
        return RegulationDoc(
            regulation_id=regulation_id,
            title=meta["title"],
            source_url=meta.get("source_url", ""),
            effective_from=meta.get("effective_from"),
            text=text,
            clauses=self._parse_clauses(regulation_id, text),
        )

    def _parse_clauses(self, regulation_id: str, text: str) -> list[Clause]:
        # Only paragraph-leading "第X条" are article headings; mid-sentence mentions
        # ("违反本法第九条规定") are cross references and must not split the document.
        matches = [
            match
            for match in ARTICLE_RE.finditer(text)
            if match.start() == 0 or text[match.start() - 1].isspace()
        ]
        clauses: list[Clause] = []
        for position, match in enumerate(matches):
            article_no = chinese_number_to_int(match.group(1))
            body_start = match.end()
            body_end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
            body = text[body_start:body_end].strip()
            if not body:
                continue
            chapter_match = CHAPTER_RE.search(text, 0, match.start())
            chapter = _clean(chapter_match.group(0)) if chapter_match else None
            clause = Clause(
                clause_id=f"{regulation_id}-ART{article_no}",
                article=f"第{match.group(1)}条",
                article_no=article_no,
                chapter=chapter,
                text=_clean(body),
                char_start=match.start(),
                char_end=body_end,
            )
            clause.items = self._parse_items(text, body_start, body_end)
            clauses.append(clause)
        return clauses

    @staticmethod
    def _parse_items(text: str, start: int, end: int) -> list[Item]:
        items: list[Item] = []
        matches = list(ITEM_RE.finditer(text, start, end))
        for position, match in enumerate(matches):
            item_start = match.end()
            item_end = matches[position + 1].start() if position + 1 < len(matches) else end
            item_text = text[item_start:item_end].strip()
            items.append(
                Item(
                    item=match.group(0),
                    index=chinese_number_to_int(match.group(1)),
                    text=_clean(item_text),
                    char_start=item_start,
                    char_end=item_end,
                )
            )
        return items

    # ------------------------------------------------------------------ rules
    def build_rules(self, docs: dict[str, RegulationDoc]) -> list[ParsedRule]:
        rules: list[ParsedRule] = []
        for spec in self._rule_specs:
            doc = docs[spec["regulation"]]
            clause = doc.clause(chinese_number_to_int(spec["article"][1:-1]))
            if clause is None:
                raise ValueError(f"rule {spec['rule_id']}: article not found: {spec['article']}")
            item = None
            if spec.get("item"):
                item = next((it for it in clause.items if it.item == spec["item"]), None)
                if item is None:
                    raise ValueError(f"rule {spec['rule_id']}: item not found: {spec['item']}")

            text = item.text if item else clause.text
            canonical_terms = self._canonical_terms(spec, text)

            penalty_min = penalty_max = None
            penalty_law_ref = None
            if spec.get("penalty_ref"):
                penalty_doc = docs[spec["regulation"]]
                penalty_clause = penalty_doc.clause(
                    chinese_number_to_int(spec["penalty_ref"]["article"][1:-1])
                )
                if penalty_clause is not None:
                    penalty_law_ref = penalty_clause.article
                    amounts = self._amount_range(penalty_clause.text)
                    if amounts:
                        penalty_min, penalty_max = amounts

            exemptions: list[str] = []
            if spec.get("exemption_ref"):
                guideline = docs.get(spec["exemption_ref"]["regulation"])
                if guideline is not None:
                    sections = self._guideline_sections(guideline.text)
                    for section in spec["exemption_ref"]["sections"]:
                        exemptions.extend(sections.get(section, []))

            rules.append(
                ParsedRule(
                    rule_id=spec["rule_id"],
                    family=spec["family"],
                    severity=spec.get("severity", "mid"),
                    regulation_id=doc.regulation_id,
                    article=clause.article,
                    item=item.item if item else None,
                    # Bind the rule to the finest clause available: item-level when the
                    # prohibition sits in a （X） item, otherwise the article itself.
                    clause_id=f"{clause.clause_id}-ITEM{item.index}" if item else clause.clause_id,
                    text=_clean(text),
                    canonical_terms=canonical_terms,
                    penalty_law_ref=penalty_law_ref,
                    penalty_min=penalty_min,
                    penalty_max=penalty_max,
                    effective_from=doc.effective_from,
                    context_exemptions=exemptions,
                    source_url=doc.source_url,
                    law=doc.title,
                )
            )
        return rules

    def _canonical_terms(self, spec: dict, rule_text: str) -> list[str]:
        source = spec.get("canonical_terms_source", "quoted_and_family")
        terms: list[str] = []
        if "quoted" in source:
            terms.extend(QUOTED_RE.findall(rule_text))
        if "family" in source:
            family = self._families.get(spec["family"])
            if family is not None:
                terms.extend(term.canonical for term in family.terms)
        # de-duplicate while keeping statute order (quoted terms first)
        return list(dict.fromkeys(terms))

    @staticmethod
    def _amount_range(text: str) -> tuple[int, int] | None:
        match = AMOUNT_RANGE_RE.search(text)
        if not match:
            return None
        minimum = chinese_number_to_int(match.group(1))
        maximum = chinese_number_to_int(match.group(2))
        if minimum > maximum:
            return None
        return minimum, maximum

    @staticmethod
    def _guideline_sections(text: str) -> dict[str, list[str]]:
        """Split a guideline into numbered sections; each section -> its （X） items."""
        matches = list(GUIDELINE_SECTION_RE.finditer(text))
        sections: dict[str, list[str]] = {}
        for position, match in enumerate(matches):
            section_no = match.group(1)
            body_end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
            body = text[match.end() : body_end]
            items = ClauseParser._parse_items(body, 0, len(body))
            if items:
                sections[section_no] = [_clean(item.text) for item in items]
            else:
                sections[section_no] = [_clean(body)]
        return sections

    def parse_all(self) -> tuple[dict[str, RegulationDoc], dict[str, list[ParsedRule]]]:
        docs = {regulation_id: self.load(regulation_id) for regulation_id in self._regulations}
        rules_by_regulation: dict[str, list[ParsedRule]] = {regulation_id: [] for regulation_id in docs}
        for rule in self.build_rules(docs):
            rules_by_regulation[rule.regulation_id].append(rule)
        return docs, rules_by_regulation

    @staticmethod
    def to_dict(docs: dict[str, RegulationDoc], rules: dict[str, list[ParsedRule]]) -> dict:
        return {
            "regulations": [
                {
                    "id": doc.regulation_id,
                    "title": doc.title,
                    "source_url": doc.source_url,
                    "effective_from": doc.effective_from,
                    "char_count": len(doc.text),
                    "clauses": [asdict(clause) for clause in doc.clauses],
                }
                for doc in docs.values()
            ],
            "rules": [asdict(rule) for doc_rules in rules.values() for rule in doc_rules],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse regulation text into clauses + structured rules")
    parser.add_argument("--out", type=Path, default=DEFAULT_DATA_DIR / "parsers" / "parsed_m2.json")
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    args = parser.parse_args()

    clause_parser = ClauseParser(args.map)
    docs, rules = clause_parser.parse_all()
    payload = ClauseParser.to_dict(docs, rules)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"output : {args.out}")
    for doc in docs.values():
        print(f"  {doc.regulation_id}: {len(doc.clauses)} clauses")
    for regulation_id, doc_rules in rules.items():
        for rule in doc_rules:
            print(f"  rule {rule.rule_id}: family={rule.family} penalty={rule.penalty_min}-{rule.penalty_max} "
                  f"terms={len(rule.canonical_terms)} exemptions={len(rule.context_exemptions)}")


if __name__ == "__main__":
    main()
