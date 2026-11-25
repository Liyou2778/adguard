"""FR-6: cross-modal consistency between the merchant caption and the creative's OCR text.

Pipeline:
1. deterministic assertion extraction for the claim families that cause most ad-law problems
   (price, discount, gift, quantity, specification, validity) -> contradictions are reported
   with both evidence snippets;
2. if the local rules find nothing but both sides contain promotional claims, optionally ask an
   OpenAI-compatible LLM (DeepSeek by default) for factual contradictions (semantic gray zone).
   The LLM is a pluggable enhancement: when it is not configured or fails, the endpoint still
   returns rule-based conflicts and marks itself degraded.

The gateway turns each conflict into a `cross_modal_consistency` violation and routes the asset
to manual review (FR-6 policy: consistency conflicts never block automatically).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

# --------------------------------------------------------------------------- data model


@dataclass
class Assertion:
    attribute: str
    key: str  # normalised attribute identity, e.g. "price" or "spec:ml"
    value: Any
    number: float | None
    text: str
    start: int
    end: int


@dataclass
class Conflict:
    attribute: str
    caption_value: str
    image_value: str
    evidence_caption: str
    evidence_image: str
    confidence: float
    source: str  # rule | llm
    span_image: tuple[int, int] | None = None
    bbox: list[float] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.span_image is not None:
            data["span_image"] = list(self.span_image)
        return data


@dataclass
class ConsistencyResult:
    conflicts: list[Conflict] = field(default_factory=list)
    degraded: bool = False
    llm_used: bool = False
    warnings: list[str] = field(default_factory=list)
    engine: str = "rules"

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflicts": [c.to_dict() for c in self.conflicts],
            "degraded": self.degraded,
            "llm_used": self.llm_used,
            "warnings": self.warnings,
            "engine": self.engine,
        }


# --------------------------------------------------------------------------- rule extraction

_NUMBER = r"(\d+(?:\.\d+)?)"
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_NUM_TOKEN = r"(\d+(?:\.\d+)?|[零一二两三四五六七八九十]+)"


def _to_number(token: str) -> float:
    """Convert an Arabic or simple Chinese numeral (1-99) to float."""
    if not token:
        return 0.0
    if re.fullmatch(r"\d+(?:\.\d+)?", token):
        return float(token)
    if token in _CN_DIGITS:
        return float(_CN_DIGITS[token])
    if "十" in token:
        left, right = token.split("十", 1)
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return float(tens * 10 + ones)
    return 0.0


def _price_assertions(text: str) -> list[Assertion]:
    out = []
    pattern = re.compile(
        r"(?:到手价|活动价|售价|原价|价格|只要|仅需|低至)?\s*[¥￥]?\s*"
        + _NUM_TOKEN + r"\s*(?:元|块钱?)")
    for match in pattern.finditer(text):
        value = _to_number(match.group(1))
        if value <= 0:
            continue
        out.append(Assertion("price", "price", f"{value:g}", value, match.group(0).strip(),
                             match.start(), match.end()))
    return out


def _discount_assertions(text: str) -> list[Assertion]:
    out = []
    for match in re.finditer(_NUM_TOKEN + r"\s*折", text):
        value = _to_number(match.group(1))
        if 0 < value <= 10:
            out.append(Assertion("discount", "discount", f"{value / 10:g}", value,
                                 match.group(0).strip(), match.start(), match.end()))
    for match in re.finditer(r"满\s*" + _NUM_TOKEN + r"\s*减\s*" + _NUM_TOKEN, text):
        left, right = _to_number(match.group(1)), _to_number(match.group(2))
        key = f"man{left:g}jian{right:g}"
        out.append(Assertion("discount", key, key, right, match.group(0).strip(),
                             match.start(), match.end()))
    return out


def _gift_assertions(text: str) -> list[Assertion]:
    out = []
    for match in re.finditer(r"买\s*" + _NUM_TOKEN + r"\s*送\s*" + _NUM_TOKEN, text):
        buy, gift = _to_number(match.group(1)), _to_number(match.group(2))
        key = f"buy{buy:g}_gift{gift:g}"
        out.append(Assertion("gift", key, key, gift, match.group(0).strip(),
                             match.start(), match.end()))
    for match in re.finditer(r"第\s*" + _NUM_TOKEN + r"\s*件\s*半价", text):
        nth = _to_number(match.group(1))
        key = f"nth{nth:g}_half"
        out.append(Assertion("gift", key, key, nth, match.group(0).strip(),
                             match.start(), match.end()))
    for match in re.finditer(r"送\s*" + _NUM_TOKEN + r"\s*件", text):
        gift = _to_number(match.group(1))
        key = f"gift{gift:g}"
        out.append(Assertion("gift", key, key, gift, match.group(0).strip(),
                             match.start(), match.end()))
    return out


def _spec_assertions(text: str) -> list[Assertion]:
    units = {"ml": "ml", "毫升": "ml", "g": "g", "克": "g", "kg": "kg", "千克": "kg",
             "l": "l", "升": "l", "片": "片", "包": "包", "瓶": "瓶", "袋": "袋", "支": "支"}
    out = []
    pattern = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|毫升|kg|千克|g|克|L|升|片|包|瓶|袋|支)",
                         re.IGNORECASE)
    for match in pattern.finditer(text):
        unit = units.get(match.group(2).lower(), match.group(2))
        value = float(match.group(1))
        out.append(Assertion("spec", f"spec:{unit}", f"{value:g}{unit}", value,
                             match.group(0).strip(), match.start(), match.end()))
    return out


def _validity_assertions(text: str) -> list[Assertion]:
    units = {"天": "day", "日": "day", "个月": "month", "月": "month", "年": "year"}
    out = []
    pattern = re.compile(r"(?:限时|有效|活动|仅限)?\s*" + _NUM_TOKEN
                         + r"\s*(天|日|个月|月|年)\s*(?:内|有效|活动)?")
    for match in pattern.finditer(text):
        unit = units[match.group(2)]
        value = _to_number(match.group(1))
        if value <= 0:
            continue
        out.append(Assertion("validity", f"validity:{unit}", f"{value:g}{match.group(2)}",
                             value, match.group(0).strip(), match.start(), match.end()))
    return out

_EXTRACTORS = (_price_assertions, _discount_assertions, _gift_assertions,
               _spec_assertions, _validity_assertions)
PROMO_HINTS = ("活动", "优惠", "折", "送", "减", "价", "元", "%", "买", "抢", "限时", "赠", "特价")


def extract_assertions(text: str) -> list[Assertion]:
    assertions: list[Assertion] = []
    for extractor in _EXTRACTORS:
        assertions.extend(extractor(text or ""))
    return assertions


def _same_value(attribute: str, key: str, left: Assertion, right: Assertion) -> bool:
    if attribute in {"gift"}:
        return left.key == right.key
    if left.number is None or right.number is None:
        return left.key == right.key
    tolerance = 0.0
    if attribute == "price":
        tolerance = max(1.0, abs(left.number) * 0.01)
    elif attribute in {"spec", "discount"}:
        tolerance = max(0.01, abs(left.number) * 0.01)
    return abs(left.number - right.number) <= tolerance


def rule_conflicts(caption: str, image_text: str) -> list[Conflict]:
    caption_assertions = extract_assertions(caption)
    image_assertions = extract_assertions(image_text)
    conflicts: list[Conflict] = []
    seen: set[tuple[str, str]] = set()
    for left in caption_assertions:
        for right in image_assertions:
            if left.attribute != right.attribute:
                continue
            # Gift/quantity promises are mutually exclusive by nature (买一送一 vs 第二件半价),
            # so differing keys are a conflict; other families only compare like-for-like keys
            # (e.g. 500ml vs 750ml, 7天 vs 30天).
            exclusive = left.attribute == "gift" or (
                left.attribute == "discount" and left.key.startswith("man"))
            if not exclusive and left.key != right.key:
                continue
            if _same_value(left.attribute, left.key, left, right):
                continue
            dedupe = (left.text, right.text)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            conflicts.append(Conflict(
                attribute=left.attribute,
                caption_value=left.value,
                image_value=right.value,
                evidence_caption=left.text,
                evidence_image=right.text,
                confidence=0.95,
                source="rule",
                span_image=(right.start, right.end),
            ))
    return conflicts


# --------------------------------------------------------------------------- LLM gray zone

_LLM_LOCK = threading.Lock()
_LLM_CACHE: dict[str, list[dict[str, Any]]] = {}
_LLM_PROMPT = """你是广告合规审核助手。比较“商家文案”和“图片文字”中的事实性承诺是否互相矛盾。
只报告事实性冲突（价格、折扣、赠品、数量、规格、有效期、活动规则），不要报告语气、标点或同义改写。
输入 JSON：
{"caption": "...", "image_text": "..."}
只输出 JSON 数组，每项 {"attribute":"price|discount|gift|spec|validity|other",
"caption_value":"...","image_value":"...","evidence_caption":"原文片段","evidence_image":"原文片段","confidence":0.0}
没有冲突时输出 []。"""


def _llm_config() -> tuple[str, str, str] | None:
    url = os.environ.get("ADGUARD_CONSISTENCY_LLM_URL", "").strip().rstrip("/")
    key = os.environ.get("ADGUARD_CONSISTENCY_LLM_KEY", "").strip()
    model = os.environ.get("ADGUARD_CONSISTENCY_LLM_MODEL", "").strip()
    if not url or not key or not model:
        return None
    return url, key, model


def llm_available() -> bool:
    return _llm_config() is not None


def _llm_conflicts(caption: str, image_text: str) -> tuple[list[dict[str, Any]], str | None]:
    config = _llm_config()
    if config is None:
        return [], "llm not configured"
    url, key, model = config
    cache_key = hashlib.sha256(f"{caption}\x00{image_text}".encode("utf-8")).hexdigest()
    with _LLM_LOCK:
        if cache_key in _LLM_CACHE:
            return _LLM_CACHE[cache_key], None
    timeout = float(os.environ.get("ADGUARD_CONSISTENCY_LLM_TIMEOUT", "20"))
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 512,
        "messages": [
            {"role": "system", "content": _LLM_PROMPT},
            {"role": "user", "content": json.dumps(
                {"caption": caption, "image_text": image_text}, ensure_ascii=False)},
        ],
    }
    try:
        response = httpx.post(f"{url}/chat/completions", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        }, json=payload, timeout=timeout)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"].get("content") or ""
    except Exception as exc:  # noqa: BLE001 - always degrade, never fail the judgment
        return [], f"llm unavailable: {exc}"
    items = _parse_llm_json(content)
    with _LLM_LOCK:
        _LLM_CACHE[cache_key] = items
    return items, None


def _parse_llm_json(content: str) -> list[dict[str, Any]]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.S)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    if not isinstance(parsed, list):
        return []
    result: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        evidence_caption = str(item.get("evidence_caption", "")).strip()
        evidence_image = str(item.get("evidence_image", "")).strip()
        if not evidence_caption or not evidence_image:
            continue
        result.append({
            "attribute": str(item.get("attribute", "other")),
            "caption_value": str(item.get("caption_value", evidence_caption))[:200],
            "image_value": str(item.get("image_value", evidence_image))[:200],
            "evidence_caption": evidence_caption[:300],
            "evidence_image": evidence_image[:300],
            "confidence": float(item.get("confidence", 0.6) or 0.6),
        })
    return result


def _span_of(image_text: str, evidence: str) -> tuple[int, int] | None:
    index = image_text.find(evidence)
    if index < 0:
        return None
    return index, index + len(evidence)


def check_consistency(caption: str, image_text: str, lines: list[dict[str, Any]] | None = None,
                      use_llm: bool | None = None) -> ConsistencyResult:
    caption = (caption or "").strip()
    image_text = (image_text or "").strip()
    result = ConsistencyResult()
    if not caption or not image_text:
        result.warnings.append("caption or image text is empty; consistency check skipped")
        return result

    result.conflicts = rule_conflicts(caption, image_text)
    rule_found = bool(result.conflicts)
    want_llm = use_llm if use_llm is not None else True
    if want_llm and (not rule_found):
        items, warning = _llm_conflicts(caption, image_text)
        if warning:
            result.degraded = True
            result.warnings.append(warning)
        else:
            result.llm_used = True
            result.engine = "rules+llm"
            for item in items:
                span = _span_of(image_text, item["evidence_image"])
                result.conflicts.append(Conflict(
                    attribute=item["attribute"],
                    caption_value=item["caption_value"],
                    image_value=item["image_value"],
                    evidence_caption=item["evidence_caption"],
                    evidence_image=item["evidence_image"],
                    confidence=max(0.0, min(1.0, item["confidence"])),
                    source="llm",
                    span_image=span,
                ))
    if lines:
        _attach_bboxes(result.conflicts, lines)
    return result


def _promotional(text: str) -> bool:
    return any(hint in text for hint in PROMO_HINTS)


def _attach_bboxes(conflicts: list[Conflict], lines: list[dict[str, Any]]) -> None:
    offsets: list[int] = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(str(line.get("text", ""))) + 1
    for conflict in conflicts:
        if conflict.span_image is None:
            continue
        start = conflict.span_image[0]
        for index in range(len(lines) - 1, -1, -1):
            if start >= offsets[index]:
                bbox = lines[index].get("bbox")
                if isinstance(bbox, list) and len(bbox) == 4:
                    conflict.bbox = [float(value) for value in bbox]
                break
