"""Client for the M4 judge model served through an OpenAI-compatible endpoint (vLLM/SGLang).

M5 integration: the rule judge stays authoritative for structured rules; the model is a
*second opinion* used conservatively (see RuleJudge hybrid policy):
  * rule=block            -> block (model ignored)
  * rule=pass             -> model block -> manual (human confirms semantic risk)
  * rule=manual(exemption)-> model pass  -> pass (model confirms the exemption)
  * rule=manual(no rule)  -> model block -> manual (still no clause-level ground truth)

Every model citation is re-validated against the local rule catalogue: unknown rule_id,
wrong clause_id, missing evidence or malformed JSON all make the verdict unusable, so no
fabricated clause/penalty can ever reach the API response.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..model_prompt import system_prompt, user_prompt
from .clause_source import DEFAULT_PARSED

MODEL_JUDGE_VERSION = "hybrid-m5-v1"
_RULE_ID_RE = re.compile(r"^ADV-LAW-ART(9-3|28)$")


class ModelJudgeError(RuntimeError):
    pass


@dataclass
class ModelVerdict:
    decision: str
    violations: list[dict[str, Any]] = field(default_factory=list)
    raw: str = ""


class ModelJudgeClient:
    def __init__(self, base_url: str, model: str = "adguard-judge", timeout: float = 8.0,
                 max_tokens: int = 384) -> None:
        self._base_url = base_url.rstrip("/")
        if not self._base_url.endswith("/v1"):
            self._base_url += "/v1"
        self._model = model
        self._timeout = timeout
        self._max_tokens = max_tokens

    def judge(self, text: str) -> ModelVerdict:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": user_prompt(text)},
            ],
            "max_tokens": self._max_tokens,
            "temperature": 0.0,
        }
        try:
            response = httpx.post(f"{self._base_url}/chat/completions", json=payload,
                                  timeout=self._timeout)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelJudgeError(f"model request failed: {exc}") from exc
        return _parse_verdict(content)


def _parse_verdict(raw: str) -> ModelVerdict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ModelJudgeError(f"model output is not JSON: {raw[:160]!r}")
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ModelJudgeError(f"model output is not valid JSON: {exc}") from exc
    decision = payload.get("decision")
    if decision not in ("block", "pass"):
        raise ModelJudgeError(f"model decision invalid: {decision!r}")
    violations = payload.get("violations") or []
    if not isinstance(violations, list):
        raise ModelJudgeError("model violations must be a list")
    if decision == "block" and not violations:
        raise ModelJudgeError("model block without violations")
    return ModelVerdict(decision=decision, violations=violations, raw=raw)


def validate_verdict(verdict: ModelVerdict, rules_by_clause: dict[str, dict[str, Any]]) -> ModelVerdict:
    """Keep only citations that exist in the local rule catalogue; rewrite penalties from data.

    Anything that cannot be validated is dropped; a block verdict with no valid citation is
    downgraded to unusable (raises ModelJudgeError) so callers fall back to the rule result.
    """
    validated: list[dict[str, Any]] = []
    for item in verdict.violations:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("rule_id", ""))
        clause_id = str(item.get("clause_id", ""))
        if not _RULE_ID_RE.match(rule_id):
            continue
        rule = rules_by_clause.get(clause_id)
        if rule is None or rule.get("rule_id") != rule_id:
            continue
        evidence = str(item.get("evidence") or "").strip()
        if not evidence:
            continue
        span = item.get("span")
        span_start = span_end = None
        if isinstance(span, list) and len(span) == 2 and all(isinstance(v, int) for v in span):
            span_start, span_end = span[0], span[1]
        validated.append(
            {
                "rule_id": rule["rule_id"],
                "family": rule.get("family", ""),
                "canonical": evidence,
                "variant_kind": "model",
                "span": [span_start, span_end] if span_start is not None else [],
                "evidence": evidence,
                "clause_id": rule["clause_id"],
                "regulation_id": rule.get("regulation_id", ""),
                "article": rule.get("article", ""),
                "item_label": rule.get("item", ""),
                "clause_revision": rule.get("clause_revision", ""),
                "clause_text": rule.get("text", ""),
                "penalty_law_ref": rule.get("penalty_law_ref"),
                "penalty_min": rule.get("penalty_min"),
                "penalty_max": rule.get("penalty_max"),
                "confidence": 0.75,
                "source": "model",
            }
        )
    if verdict.decision == "block" and not validated:
        raise ModelJudgeError("model block has no citation valid in the rule catalogue")
    return ModelVerdict(decision=verdict.decision, violations=validated, raw=verdict.raw)


def load_rules_by_clause(path: Path | None = None) -> dict[str, dict[str, Any]]:
    source = Path(path or DEFAULT_PARSED)
    if not source.exists():
        return {}
    payload = json.loads(source.read_text(encoding="utf-8"))
    return {rule["clause_id"]: rule for rule in payload.get("rules", [])}
