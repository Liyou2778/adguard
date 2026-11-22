"""Evaluate the normalization+matcher pipeline on the M2 dataset and emit a report.

Metrics:
    hit rate      share of planted variants whose expected family was detected with an
                  overlapping original span (the PRD M2 gate: >= 0.95)
    per family    hit rate per attack family (to expose weak spots, not just an average)
    false positive rate on compliant copy
    unknown set    honest coverage number for paraphrases/unseen homophones that are
                  OUT of the lexical dictionary (expected low; M4 semantic model)

Exit code 1 if the variant hit-rate gate is not met, so it can run in CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from .dataset import DEFAULT_OUT
from .matcher import Matcher

GATE = 0.95
DEFAULT_REPORT = Path(__file__).resolve().parents[4] / "docs" / "M2-变体归一化评测.md"


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def evaluate(dataset_path: Path, report_path: Path | None = None) -> dict:
    raw = dataset_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    samples = [json.loads(line) for line in raw.splitlines() if line.strip()]
    matcher = Matcher()

    variant_stats: dict[str, dict[str, int]] = {}
    unknown_stats: dict[str, dict[str, int]] = {}
    negative_total = 0
    negative_false_positives = 0
    failures: list[dict] = []

    for sample in samples:
        result = matcher.match(sample["text"])
        kind = sample["kind"]
        expected_family = sample["expected_family"]
        planted = (sample["span_start"], sample["span_end"])

        hits = [
            detection
            for detection in result.detections
            if detection.family == expected_family and _overlap(detection.span, planted) > 0
        ]

        if kind == "negative":
            negative_total += 1
            if result.detections:
                negative_false_positives += 1
                failures.append(
                    {
                        "id": sample["id"],
                        "reason": "false_positive",
                        "text": sample["text"],
                        "detections": [d.evidence for d in result.detections],
                    }
                )
            continue

        bucket = variant_stats if kind == "variant" else unknown_stats
        family = sample["variant_family"]
        entry = bucket.setdefault(family, {"total": 0, "hits": 0})
        entry["total"] += 1
        if hits:
            entry["hits"] += 1
            # Compare canonical forms after normalization: a traditional-spelling
            # sample (無出其右) and its simplified canonical (无出其右) are the same term.
            expected_canonical = matcher.normalize_only(sample["term"])
            canonical_ok = any(
                matcher.normalize_only(hit.canonical) == expected_canonical for hit in hits
            )
            entry.setdefault("canonical_ok", 0)
            if canonical_ok:
                entry["canonical_ok"] += 1
        else:
            if len(failures) < 200:
                failures.append(
                    {
                        "id": sample["id"],
                        "reason": "miss",
                        "term": sample["term"],
                        "text": sample["text"],
                        "normalized": result.normalized.text,
                    }
                )

    def summarize(bucket: dict[str, dict[str, int]]) -> dict:
        total = sum(entry["total"] for entry in bucket.values())
        hits = sum(entry["hits"] for entry in bucket.values())
        per_family = {
            name: {
                "total": entry["total"],
                "hits": entry["hits"],
                "rate": round(entry["hits"] / entry["total"], 4) if entry["total"] else 0.0,
                "canonical_ok": entry.get("canonical_ok", 0),
            }
            for name, entry in sorted(bucket.items())
        }
        return {
            "total": total,
            "hits": hits,
            "rate": round(hits / total, 4) if total else 0.0,
            "per_family": per_family,
        }

    metrics = {
        "dataset": str(dataset_path),
        "dataset_sha256": digest,
        "dataset_samples": len(samples),
        "matcher_version": matcher.version,
        "variant": summarize(variant_stats),
        "unknown": summarize(unknown_stats),
        "negative": {
            "total": negative_total,
            "false_positives": negative_false_positives,
            "false_positive_rate": round(negative_false_positives / negative_total, 4)
            if negative_total
            else 0.0,
        },
        "failures_sample": failures[:50],
        "gate": GATE,
        "gate_passed": (summarize(variant_stats)["rate"] >= GATE),
    }

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_markdown(metrics), encoding="utf-8")

    return metrics


def render_markdown(metrics: dict) -> str:
    variant = metrics["variant"]
    unknown = metrics["unknown"]
    negative = metrics["negative"]
    status = "PASS" if metrics["gate_passed"] else "FAIL"

    lines = [
        "# M2 变体归一化评测报告",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}（由 `adguard_pipeline.evaluation` 自动生成）",
        f"> 数据集：`{metrics['dataset']}`",
        f"> 数据集 sha256：`{metrics['dataset_sha256']}`",
        f"> 匹配器版本：`{metrics['matcher_version']}`",
        f"> 验收门禁：变体命中率 ≥ **{metrics['gate']:.0%}** → **{status}**",
        "",
        "## 方法",
        "",
        "1. 数据集由 `dataset.py` 生成：把**已知 7 类变形**（拼音/拆字/谐音/emoji 间隔/零宽字符/异体字与全半角/同义词替换）"
        "注入到营销文案模板中，并记录被注入片段的**原文 span**；",
        "2. 匹配器对原文跑归一化 + AC 多模式匹配，输出 family + 原文 span；",
        "3. **命中判定**：检测结果与注入 span 有重叠，且 family 一致 —— 只看“有没有检到”不算命中，"
        "必须定位到正确区域（这是 FR-8 条款级定位的前提）；",
        "4. 另外两套集合为诚实性检查：**负样本**（合规文案，衡量误报）、"
        "**未知集**（词典之外的语义改写/未见谐音，预期低，交给 M4 语义模型）。",
        "",
        "## 总体结果",
        "",
        "| 集合 | 样本数 | 命中 | 命中率 |",
        "|---|---:|---:|---:|",
        f"| 变体集（验收门禁） | {variant['total']} | {variant['hits']} | **{variant['rate']:.2%}** |",
        f"| 未知集（超出词典承诺范围） | {unknown['total']} | {unknown['hits']} | {unknown['rate']:.2%} |",
        f"| 负样本（合规文案） | {negative['total']} | 误报 {negative['false_positives']} | 误报率 {negative['false_positive_rate']:.2%} |",
        "",
        "## 分族命中率",
        "",
        "| 变形族 | 样本数 | 命中 | 命中率 | 命中 canonical |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, entry in variant["per_family"].items():
        lines.append(
            f"| {name} | {entry['total']} | {entry['hits']} | {entry['rate']:.2%} | {entry['canonical_ok']} |"
        )
    lines += [
        "",
        "## 未知集明细（诚实记录，不计入门禁）",
        "",
        "| 类型 | 样本数 | 命中 | 命中率 |",
        "|---|---:|---:|---:|",
    ]
    for name, entry in unknown["per_family"].items():
        lines.append(f"| {name} | {entry['total']} | {entry['hits']} | {entry['rate']:.2%} |")
    lines += [
        "",
        "## 结论与局限",
        "",
        f"1. 已知 7 类变形在 **{variant['total']}** 条样本上命中率 **{variant['rate']:.2%}**，"
        f"{'达到' if metrics['gate_passed'] else '未达到'} PRD M2 门禁（≥95%）；",
        "2. 拼音族依赖“词典词 → 拼音形态”预生成，因此**同音异形词**命中率高，但**生僻同音字**会落入未知集；",
        "3. 语义改写（无出其右 → 无人能及 之类）不在词典承诺范围（PRD R-2：只承诺已知变体族），"
        "由 M4 语义模型兜底，在本报告未知集中如实给出低命中率；",
        "4. 负样本误报率用于后续 M4 门禁的误杀基线；当前规则匹配的误报主要来自词本身被合规语境使用（如“第一”用于序数），"
        "需要在 M3 条款检索 / M4 模型判定中结合语境处理。",
        "",
        "## 复现",
        "",
        "```powershell",
        "cd services\\pipeline-py",
        ".\\.venv\\Scripts\\python -m adguard_pipeline.dataset",
        ".\\.venv\\Scripts\\python -m adguard_pipeline.evaluation --report ..\\..\\docs\\M2-变体归一化评测.md",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate M2 normalization/matching metrics")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    metrics = evaluate(args.dataset, args.report)
    if args.json_out:
        args.json_out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: metrics[k] for k in ("variant", "unknown", "negative", "gate", "gate_passed")},
                     ensure_ascii=False, indent=2))
    print(f"report: {args.report}")
    raise SystemExit(0 if metrics["gate_passed"] else 1)


if __name__ == "__main__":
    main()
