"""Clause-parser accuracy evaluation against the hand-labelled gold set (M2 gate: >= 90%).

Every check is one unit of the metric. The check list is intentionally explicit
(article segmentation, item segmentation, per-field rule assertions) so a failure
points at exactly which extraction rule is wrong.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .clause_parser import DEFAULT_DATA_DIR, ClauseParser, chinese_number_to_int

GATE = 0.90
DEFAULT_GOLD = DEFAULT_DATA_DIR / "regulations" / "gold" / "m2_gold.json"
DEFAULT_REPORT = Path(__file__).resolve().parents[4] / "docs" / "M2-条款解析评测.md"


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


def _check(checks: list[Check], name: str, condition: bool, detail: str = "") -> None:
    checks.append(Check(name=name, passed=bool(condition), detail=detail))


def evaluate(gold_path: Path = DEFAULT_GOLD, report_path: Path | None = DEFAULT_REPORT) -> dict:
    gold = json.loads(Path(gold_path).read_text(encoding="utf-8"))
    parser = ClauseParser()
    docs, rules_by_regulation = parser.parse_all()
    all_rules = {rule.rule_id: rule for doc_rules in rules_by_regulation.values() for rule in doc_rules}
    checks: list[Check] = []

    law = docs["ADV-LAW"]
    parsed_articles = {clause.article_no for clause in law.clauses}
    for article_no in gold["articles"]:
        _check(checks, f"article 第{article_no}条 segmented",
               article_no in parsed_articles,
               "" if article_no in parsed_articles else f"missing; parsed={sorted(parsed_articles)}")

    for article_no, expected_count in gold["item_counts"].items():
        clause = law.clause(int(article_no))
        actual = len(clause.items) if clause else -1
        _check(checks, f"article 第{article_no}条 item count",
               actual == expected_count, f"expected={expected_count} actual={actual}")

    # Cross references inside an article must NOT be treated as new articles.
    for regulation_id, expected_count in gold.get("clause_counts", {}).items():
        doc = docs.get(regulation_id)
        actual = len(doc.clauses) if doc else -1
        _check(checks, f"{regulation_id}: clause count (no reference splits)",
               actual == expected_count, f"expected={expected_count} actual={actual}")
    clause_57 = law.clause(57)
    if clause_57 is not None:
        first_item = next((item for item in clause_57.items if item.item == "（一）"), None)
        _check(checks, "cross reference kept inside 第五十七条（一）item",
               first_item is not None and "第九条" in first_item.text,
               f"text={(first_item.text if first_item else None)!r}")

    expected_rule_count = gold["expected_rule_count"]
    _check(checks, "no fabricated rules",
           len(all_rules) == expected_rule_count,
           f"expected={expected_rule_count} actual={len(all_rules)} ({sorted(all_rules)})")

    for expected in gold["rules"]:
        rule = all_rules.get(expected["rule_id"])
        prefix = expected["rule_id"]
        _check(checks, f"{prefix}: rule exists", rule is not None)
        if rule is None:
            continue
        _check(checks, f"{prefix}: family", rule.family == expected["family"],
               f"expected={expected['family']} actual={rule.family}")
        _check(checks, f"{prefix}: article",
               chinese_number_to_int(rule.article[1:-1]) == expected["article_no"],
               f"expected=第{expected['article_no']}条 actual={rule.article}")
        _check(checks, f"{prefix}: item", rule.item == expected["item"],
               f"expected={expected['item']} actual={rule.item}")
        _check(checks, f"{prefix}: penalty min", rule.penalty_min == expected["penalty_min"],
               f"expected={expected['penalty_min']} actual={rule.penalty_min}")
        _check(checks, f"{prefix}: penalty max", rule.penalty_max == expected["penalty_max"],
               f"expected={expected['penalty_max']} actual={rule.penalty_max}")
        _check(checks, f"{prefix}: effective_from",
               rule.effective_from == expected["effective_from"],
               f"expected={expected['effective_from']} actual={rule.effective_from}")
        quoted_ok = all(term in rule.canonical_terms for term in expected["quoted_terms"])
        _check(checks, f"{prefix}: statute-quoted terms present", quoted_ok,
               f"missing={[t for t in expected['quoted_terms'] if t not in rule.canonical_terms]}")
        _check(checks, f"{prefix}: exemption coverage",
               len(rule.context_exemptions) >= expected["min_exemptions"],
               f"expected>={expected['min_exemptions']} actual={len(rule.context_exemptions)}")
        _check(checks, f"{prefix}: rule text", expected["text_contains"] in rule.text,
               f"expected contains {expected['text_contains']!r}")

    total = len(checks)
    passed = sum(1 for check in checks if check.passed)
    accuracy = passed / total if total else 0.0
    metrics = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "gold": str(gold_path),
        "checks_total": total,
        "checks_passed": passed,
        "accuracy": round(accuracy, 4),
        "gate": GATE,
        "gate_passed": accuracy >= GATE,
        "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks],
        "regulations": {
            doc.regulation_id: {"clauses": len(doc.clauses), "char_count": len(doc.text)}
            for doc in docs.values()
        },
        "rules": [
            {
                "rule_id": rule.rule_id,
                "family": rule.family,
                "article": rule.article,
                "item": rule.item,
                "canonical_terms": len(rule.canonical_terms),
                "penalty": [rule.penalty_min, rule.penalty_max],
                "exemptions": len(rule.context_exemptions),
                "effective_from": rule.effective_from,
            }
            for rule in all_rules.values()
        ],
    }
    if report_path is not None:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(render_markdown(metrics), encoding="utf-8")
    return metrics


def render_markdown(metrics: dict) -> str:
    status = "PASS" if metrics["gate_passed"] else "FAIL"
    lines = [
        "# M2 条款解析评测报告",
        "",
        f"> 生成时间：{metrics['generated_at']}（由 `adguard_pipeline.clause_eval` 自动生成）",
        f"> 金标文件：`{metrics['gold']}`",
        f"> 验收门禁：条款解析准确率 ≥ **{metrics['gate']:.0%}** → **{status}**",
        "",
        "## 方法",
        "",
        "1. 输入：官方公开文本 fixture（`data/regulations/`）——`广告法` 选段 11 条 + `广告绝对化用语执法指南` 全文；",
        "2. 解析器：按 `第X条` / `（X）` / `第X章` 锚点切分，用显式正则抽取罚则金额、生效日期、引号内禁用词、豁免情形；",
        "3. 金标：按官方原文**逐条手工标注**（`data/regulations/gold/m2_gold.json`），共 "
        f"**{metrics['checks_total']} 个检查点**（条切分 / 项切分 / 规则字段逐项比对）；",
        "4. 准确率 = 通过检查点 / 总检查点；**不足 90% 直接 fail**（CI 可挂）。",
        "",
        "> 说明：本环境没有第三方人工评审资源，金标由 AI 依据官方公开文本逐条转录并执行比较，"
        "属于“可复核的自动比对”；M3 会把金标集交给外部人工抽检复核，并在报告中更新结论。",
        "",
        "## 结果",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 检查点 | {metrics['checks_total']} |",
        f"| 通过 | {metrics['checks_passed']} |",
        f"| 准确率 | **{metrics['accuracy']:.2%}** |",
        f"| 门禁 | ≥ {metrics['gate']:.0%} → **{status}** |",
        "",
        "### 解析规模",
        "",
        "| 法规 | 条款数 | 字数 |",
        "|---|---:|---:|",
    ]
    for regulation_id, info in metrics["regulations"].items():
        lines.append(f"| {regulation_id} | {info['clauses']} | {info['char_count']} |")
    lines += [
        "",
        "### 产出的结构化规则",
        "",
        "| rule_id | family | 条款 | 罚则区间(元) | 禁用词数 | 豁免条数 | 生效日期 |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for rule in metrics["rules"]:
        lines.append(
            f"| {rule['rule_id']} | {rule['family']} | {rule['article']} {rule['item'] or ''} | "
            f"{rule['penalty'][0]}–{rule['penalty'][1]} | {rule['canonical_terms']} | "
            f"{rule['exemptions']} | {rule['effective_from']} |"
        )
    lines += [
        "",
        "### 检查点明细",
        "",
        "| 检查点 | 结果 | 说明 |",
        "|---|---|---|",
    ]
    for check in metrics["checks"]:
        result = "PASS" if check["passed"] else "FAIL"
        detail = check["detail"].replace("|", "\\|") if check["detail"] else ""
        lines.append(f"| {check['name']} | {result} | {detail} |")
    lines += [
        "",
        "## 复现",
        "",
        "```powershell",
        "cd services\\pipeline-py",
        ".\\.venv\\Scripts\\python -m adguard_pipeline.clause_parser",
        ".\\.venv\\Scripts\\python -m adguard_pipeline.clause_eval",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate M2 clause parser accuracy")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    metrics = evaluate(args.gold, args.report)
    if args.json_out:
        args.json_out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: metrics[k] for k in ("checks_total", "checks_passed", "accuracy", "gate_passed")},
                     ensure_ascii=False, indent=2))
    print(f"report: {args.report}")
    raise SystemExit(0 if metrics["gate_passed"] else 1)


if __name__ == "__main__":
    main()
