"""M3 rule-based judge: M2 matcher + M3 clause retrieval -> decision with clause evidence.

This is the first end-to-end judging path that can actually block an asset:

    text -> family detections (M2) -> clause retrieval (M3) -> rule/penalty lookup
         -> decision (block / manual / pass) + clause-level violations

Deliberate limits (M4 will extend):
* only clauses that have a structured rule+penalty in the M2 rule library can produce
  a `block`; other family hits are routed to `manual` (no fabricated penalties);
* no semantic/context model yet: an absolute term used in an exempted context is
  caught by a conservative exemption-hint guard (only hints, not proof) and routed to
  manual review instead of blocking;
* multimodal assets (image/video) are not reachable here - OCR is not implemented
  yet, so the gateway routes them to manual before calling this service.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..matcher import Detection, Matcher
from ..normalization import Normalizer
from .clause_source import DEFAULT_PARSED
from .model_judge import (MODEL_JUDGE_VERSION, ModelJudgeClient, ModelJudgeError,
                          load_rules_by_clause, validate_verdict)
from .models import RetrievalHit, RetrievalResult
from .service import RetrievalService

JUDGE_VERSION = "rule-m3-v1"

# Detection variant kinds ordered by how directly they prove the evasion was deliberate.
_VARIANT_CONFIDENCE = {
    "canonical": 0.95,
    "variant": 0.92,
    "homophone": 0.88,
    "split_char": 0.88,
    "pinyin": 0.86,
    "synonym": 0.82,
}

# Conservative guard for the exemption contexts described in the 2023 enforcement
# guideline. M3 can only *notice a hint*; real exemption reasoning is M4's job.
EXEMPTION_HINT_RE = re.compile(
    r"有据可查|可证明|提供证明|历史数据|客观事实|国家标准|行业标准|地方标准|"
    r"奖项|称号|自我比较|仅用于|使用方法|保存期限|销量数据|市场占有率"
)


@dataclass
class RuleJudgment:
    decision: str
    reason: str
    violations: list[dict[str, Any]]
    judge_version: str
    retrieval_warnings: list[str]
    dropped_stale: int
    index_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "violations": self.violations,
            "judge_version": self.judge_version,
            "retrieval_warnings": self.retrieval_warnings,
            "dropped_stale": self.dropped_stale,
            "index_version": self.index_version,
        }


class RuleJudge:
    def __init__(self, retrieval: RetrievalService, matcher: Matcher | None = None,
                 normalizer: Normalizer | None = None,
                 parsed_rules_path: Path | None = None,
                 model_client: ModelJudgeClient | None = None) -> None:
        self._retrieval = retrieval
        self._matcher = matcher or Matcher()
        self._normalizer = normalizer or Normalizer.from_data()
        self._rules = _load_rule_map(Path(parsed_rules_path or DEFAULT_PARSED))
        self._model_client = model_client
        self._rules_by_clause = load_rules_by_clause(Path(parsed_rules_path or DEFAULT_PARSED))
        self._judge_version = MODEL_JUDGE_VERSION if model_client is not None else JUDGE_VERSION

    def judge(self, text: str) -> RuleJudgment:
        base = self._judge_rules(text)
        if self._model_client is None:
            return base
        return self._apply_model(text, base)

    def _judge_rules(self, text: str) -> RuleJudgment:
        text = text[:200_000]
        match = self._matcher.match(text)
        if not match.detections:
            return self._result("pass", "rule-matcher found no known-family violation; "
                                        "semantic judging is not implemented until M4")

        if EXEMPTION_HINT_RE.search(text):
            return self._result(
                "manual",
                "命中疑似违规表述，但文本包含免责语境线索（执法指南第五条/第六条情形），"
                "M3 无法证明其真实性，转人工复核",
            )

        violations: list[dict[str, Any]] = []
        warnings: list[str] = []
        dropped = 0
        for detection in _merge_detections(match.detections)[:10]:
            result = self._retrieval.retrieve(
                detection.evidence, top_k=3, family_hint=detection.family
            )
            dropped += result.dropped_stale
            warnings.extend(result.warnings)
            picked = self._pick_clause_with_rule(result, detection.family)
            if picked is None:
                continue
            clause_hit, rule = picked
            violations.append(
                {
                    "rule_id": rule["rule_id"],
                    "family": detection.family,
                    "canonical": detection.canonical,
                    "variant_kind": detection.variant_kind,
                    "span": list(detection.span),
                    "evidence": detection.evidence,
                    "clause_id": clause_hit.clause_id,
                    "regulation_id": clause_hit.regulation_id,
                    "article": clause_hit.article,
                    "item_label": clause_hit.item_label,
                    "clause_revision": clause_hit.revision,
                    "clause_text": clause_hit.text,
                    "penalty_law_ref": rule.get("penalty_law_ref"),
                    "penalty_min": rule.get("penalty_min"),
                    "penalty_max": rule.get("penalty_max"),
                    "confidence": _VARIANT_CONFIDENCE.get(detection.variant_kind, 0.8),
                }
            )

        if violations:
            return self._result(
                "block",
                f"命中 {len(violations)} 处条款级规则违规（M3 规则判定；未使用语义模型）",
                violations=violations,
                warnings=warnings,
                dropped=dropped,
            )
        return self._result(
            "manual",
            "命中疑似违规表述，但缺少可引用的结构化规则/罚则（M4 扩充规则库与语义判定），转人工",
            warnings=warnings,
            dropped=dropped,
        )

    # ------------------------------------------------------------------ helpers
    def _result(self, decision: str, reason: str, violations: list[dict[str, Any]] | None = None,
                warnings: list[str] | None = None, dropped: int = 0) -> RuleJudgment:
        return RuleJudgment(
            decision=decision,
            reason=reason,
            violations=violations or [],
            judge_version=self._judge_version,
            retrieval_warnings=sorted(set(warnings or [])),
            dropped_stale=dropped,
            index_version=self._retrieval.status().index_version,
        )

    def _apply_model(self, text: str, base: RuleJudgment) -> RuleJudgment:
        """Conservative hybrid: the model can confirm exemptions / flag semantic risk,
        but can never auto-block a phrase the rule catalogue did not already flag."""
        try:
            verdict = validate_verdict(self._model_client.judge(text), self._rules_by_clause)
        except ModelJudgeError as exc:
            return self._result(
                base.decision,
                f"{base.reason}；模型判定不可用（{exc}），沿用规则结论",
                violations=base.violations,
                warnings=base.retrieval_warnings,
                dropped=base.dropped_stale,
            )
        if base.decision == "block":
            return base
        if base.decision == "pass":
            if verdict.decision == "block":
                return self._result(
                    "manual",
                    "规则未命中，但模型判定违规（M5 保守策略：不自动 block，转人工确认）",
                    violations=verdict.violations,
                )
            return self._result(
                "pass", f"{base.reason}；模型确认无违规",
                warnings=base.retrieval_warnings, dropped=base.dropped_stale,
            )
        if verdict.decision == "pass" and "免责语境" in base.reason:
            return self._result(
                "pass", "免责语境经模型确认（M5 hybrid：规则提示 + 模型复核）",
                warnings=base.retrieval_warnings, dropped=base.dropped_stale,
            )
        if verdict.decision == "block":
            return self._result(
                "manual",
                "规则无结构化条款依据，模型判定违规（M5 保守策略：交人工复核）",
                violations=verdict.violations,
                warnings=base.retrieval_warnings, dropped=base.dropped_stale,
            )
        return self._result(
            "manual",
            f"{base.reason}；模型也未能给出结构化结论",
            warnings=base.retrieval_warnings, dropped=base.dropped_stale,
        )

    def _pick_clause_with_rule(self, result: RetrievalResult, family: str):
        for hit in result.hits:
            if family not in hit.families:
                continue
            rule = self._rules.get(hit.clause_id)
            if rule is not None:
                return hit, rule
        return None


def _merge_detections(detections: tuple[Detection, ...]) -> list[Detection]:
    """Merge overlapping detections of the same family into one violation.

    "全网最低价" fires both 全网最低 and 最低价; citing the same rule twice for one
    phrase is noise, so overlapping spans are unioned and the longer evidence wins.
    """
    ordered = sorted(detections, key=lambda d: (d.family, d.span[0], -(d.span[1] - d.span[0])))
    merged: list[Detection] = []
    for detection in ordered:
        if merged and detection.family == merged[-1].family and detection.span[0] <= merged[-1].span[1]:
            previous = merged[-1]
            if (detection.span[1] - detection.span[0]) > (previous.span[1] - previous.span[0]):
                merged[-1] = detection
            continue
        merged.append(detection)
    return merged


def _load_rule_map(path: Path) -> dict[str, dict]:
    """Map clause_id -> structured rule (from the M2 parser output)."""
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    mapping: dict[str, dict] = {}
    for rule in payload.get("rules", []):
        mapping[rule["clause_id"]] = rule
    return mapping
