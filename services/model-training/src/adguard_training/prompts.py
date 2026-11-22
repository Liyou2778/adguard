"""Prompt construction + rule catalog for SFT/DPO/distillation data.

The rule catalog is injected into the system prompt from the M2 parser output, so a
rule update changes the prompt automatically (ADR-1: rules are data).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

RULE_CATALOG_FALLBACK = """可判定规则（只有这些规则可以给出 block）：
1. rule_id=ADV-LAW-ART9-3（广告法第九条第（三）项）：不得使用“国家级”“最高级”“最佳”等绝对化用语。
   clause_id=ADV-LAW-ART9-ITEM3；罚则第五十七条；罚款 200000–1000000 元。
2. rule_id=ADV-LAW-ART28（广告法第二十八条）：虚假或者引人误解的内容构成虚假广告。
   clause_id=ADV-LAW-ART28；罚则第五十五条；罚款 200000–1000000 元。"""


def find_parsed_rules() -> Path | None:
    """Locate the M2 parser output both on Windows (monorepo) and inside WSL copies."""
    override = os.environ.get("ADGUARD_PARSED_RULES", "")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        for relative in (
            Path("pipeline-py") / "data" / "parsers" / "parsed_m2.json",
            Path("data") / "parsers" / "parsed_m2.json",
        ):
            candidate = parent / relative
            if candidate.exists():
                return candidate
    return None


PARSED_RULES = find_parsed_rules()


def load_rule_catalog(path: Path | None = None) -> str:
    source = Path(path) if path else PARSED_RULES
    if source is None or not source.exists():
        return RULE_CATALOG_FALLBACK
    payload = json.loads(source.read_text(encoding="utf-8"))
    lines: list[str] = []
    for index, rule in enumerate(payload.get("rules", []), start=1):
        penalty_law = rule.get("penalty_law_ref") or "—"
        lines.append(
            f"{index}. rule_id={rule['rule_id']}（{rule['law']} {rule['article']}"
            f"{rule.get('item') or ''}）：{rule['text']}\n"
            f"   clause_id={rule['clause_id']}；罚则{penalty_law}；"
            f"罚款 {rule.get('penalty_min')}–{rule.get('penalty_max')} 元。"
        )
    return "可判定规则（只有这些规则可以给出 block）：\n" + "\n".join(lines)


def system_prompt(rule_catalog: str | None = None) -> str:
    catalog = rule_catalog or load_rule_catalog()
    return f"""你是 AdGuard 营销素材合规初审引擎。你的任务是判断一段营销素材是否违反《中华人民共和国广告法》，并给出条款级依据。

{catalog}

判定原则：
- 命中绝对化用语或虚假广告，且不存在免责情形 → block；
- 合规文案 → pass；
- 符合《广告绝对化用语执法指南》免责情形（自我比较、奖项称号、消费提示、有据可查的客观事实等）→ pass；
- 只输出严格 JSON，不要输出解释或 markdown。

输出格式：
{{"decision":"block","violations":[{{"rule_id":"ADV-LAW-ART9-3","clause_id":"ADV-LAW-ART9-ITEM3","article":"第九条","item":"（三）","span":[start,end],"evidence":"原文片段","penalty_law_ref":"第五十七条","penalty_min":200000,"penalty_max":1000000}}]}}
或
{{"decision":"pass","violations":[]}}"""


def user_prompt(text: str, asset_type: str = "text") -> str:
    return f"素材文本：{text}\n素材类型：{asset_type}\n请输出判定 JSON。"


def build_record(text: str, output: str, asset_type: str = "text",
                 rule_catalog: str | None = None) -> dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": system_prompt(rule_catalog)},
            {"role": "user", "content": user_prompt(text, asset_type)},
            {"role": "assistant", "content": output},
        ]
    }
