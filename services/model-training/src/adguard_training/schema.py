"""Output schema + validation for the AdGuard judging model.

The model must emit strict JSON. Downstream code (gateway / evaluation) depends on
this shape, so it is validated here and never trusted blindly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

DECISIONS = ("block", "pass")

RULE_ID_RE = re.compile(r"^ADV-LAW-ART(9-3|28)$")
CLAUSE_ID_RE = re.compile(r"^ADV-LAW-ART(9|28)(-ITEM\d+)?$")

SYSTEM_PROMPT = """你是 AdGuard 营销素材合规初审引擎。你的任务是判断一段营销素材是否违反《中华人民共和国广告法》的以下条款，并给出条款级依据。

可判定规则（只有这些规则可以给出 block）：
1. rule_id=ADV-LAW-ART9-3（广告法第九条第（三）项）：广告不得使用“国家级”“最高级”“最佳”等绝对化用语。
   条款 clause_id=ADV-LAW-ART9-ITEM3；罚则依据第五十七条；罚款区间 200000–1000000 元。
2. rule_id=ADV-LAW-ART28（广告法第二十八条）：广告以虚假或者引人误解的内容欺骗、误导消费者的，构成虚假广告。
   条款 clause_id=ADV-LAW-ART28；罚则依据第五十五条；罚款区间 200000–1000000 元。

判定原则：
- 命中绝对化用语或虚假广告且没有免责情形 → block；
- 合规文案 → pass；
- 符合《广告绝对化用语执法指南》免责情形（自我比较、奖项称号、消费提示、有据可查的客观事实等）→ pass；
- 只输出 JSON，不要输出解释或 markdown。

输出格式（严格 JSON）：
{"decision":"block","violations":[{"rule_id":"ADV-LAW-ART9-3","clause_id":"ADV-LAW-ART9-ITEM3","article":"第九条","item":"（三）","span":[start,end],"evidence":"原文片段","penalty_law_ref":"第五十七条","penalty_min":200000,"penalty_max":1000000}]}
或
{"decision":"pass","violations":[]}
"""

USER_TEMPLATE = "素材文本：{text}\n素材类型：{asset_type}\n请输出判定 JSON。"


@dataclass(frozen=True)
class ParsedViolation:
    rule_id: str
    clause_id: str
    article: str
    item: str | None
    span: tuple[int, int] | None
    evidence: str
    penalty_law_ref: str | None
    penalty_min: int | None
    penalty_max: int | None


@dataclass(frozen=True)
class ParsedOutput:
    decision: str
    violations: tuple[ParsedViolation, ...]
    raw: str

    @property
    def valid(self) -> bool:
        return True


class OutputParseError(ValueError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise OutputParseError(f"no JSON object in output: {text[:200]!r}")


def parse_output(text: str) -> ParsedOutput:
    payload = _extract_json(text)
    decision = payload.get("decision")
    if decision not in DECISIONS:
        raise OutputParseError(f"invalid decision: {decision!r}")
    raw_violations = payload.get("violations") or []
    if not isinstance(raw_violations, list):
        raise OutputParseError("violations must be a list")
    violations: list[ParsedViolation] = []
    for item in raw_violations:
        if not isinstance(item, dict):
            raise OutputParseError("violation must be an object")
        rule_id = str(item.get("rule_id", ""))
        clause_id = str(item.get("clause_id", ""))
        if not RULE_ID_RE.match(rule_id):
            raise OutputParseError(f"invalid rule_id: {rule_id!r}")
        if not CLAUSE_ID_RE.match(clause_id):
            raise OutputParseError(f"invalid clause_id: {clause_id!r}")
        span = item.get("span")
        span_tuple = None
        if isinstance(span, list) and len(span) == 2 and all(isinstance(v, int) for v in span):
            span_tuple = (span[0], span[1])
        violations.append(
            ParsedViolation(
                rule_id=rule_id,
                clause_id=clause_id,
                article=str(item.get("article", "")),
                item=item.get("item"),
                span=span_tuple,
                evidence=str(item.get("evidence", "")),
                penalty_law_ref=item.get("penalty_law_ref"),
                penalty_min=item.get("penalty_min"),
                penalty_max=item.get("penalty_max"),
            )
        )
    if decision == "pass" and violations:
        raise OutputParseError("pass decision must not contain violations")
    if decision == "block" and not violations:
        raise OutputParseError("block decision requires at least one violation")
    return ParsedOutput(decision=decision, violations=tuple(violations), raw=text)


def gold_output(decision: str, violations: list[dict[str, Any]]) -> str:
    return json.dumps({"decision": decision, "violations": violations}, ensure_ascii=False)
