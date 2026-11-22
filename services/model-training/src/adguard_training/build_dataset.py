"""Build M4 training/eval data from the M2 rule teacher + public penalty-case seeds.

Why this design (PRD R-3): no real labelled corpus exists, so the teacher is the
deterministic rule stack (M2 matcher + rule catalogue), augmented with:
  * 公开行政处罚案例短引用（种子语境）;
  * 执法指南免责情形（硬负样本，专治误杀）;
  * 规则漏检的语义改写（单独成集，只为报告覆盖差距，不进门禁）.

Split protocol (leakage-safe): samples are partitioned by **term identity**
(`sha256(term) % 4 == 0` -> eval), so an eval sample never shares its
term/template pair with training. Negatives are split by withheld templates, and
two exemption examples are withheld from training to measure exemption generalisation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from adguard_pipeline.dataset import generate as generate_variants
from adguard_pipeline.matcher import Detection, Matcher
from adguard_pipeline.normalization import Normalizer
from adguard_pipeline.retrieval.rule_judge import EXEMPTION_HINT_RE
from adguard_pipeline.variants import NEGATIVE_TEMPLATES

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "data" / "generated"
SEEDS_PATH = Path(__file__).resolve().parents[2] / "data" / "seeds" / "public_cases.json"

TRULES = {
    "absolute_terms": {
        "rule_id": "ADV-LAW-ART9-3",
        "clause_id": "ADV-LAW-ART9-ITEM3",
        "article": "第九条",
        "item": "（三）",
        "penalty_law_ref": "第五十七条",
        "penalty_min": 200_000,
        "penalty_max": 1_000_000,
    },
    "false_advertising": {
        "rule_id": "ADV-LAW-ART28",
        "clause_id": "ADV-LAW-ART28",
        "article": "第二十八条",
        "item": None,
        "penalty_law_ref": "第五十五条",
        "penalty_min": 200_000,
        "penalty_max": 1_000_000,
    },
}

EVAL_TERM_FOLD = 0  # fold(term) == 0 -> held-out eval term
HOLDOUT_CASE = "case-shangcheng-2024"

CASE_CONTEXT_TEMPLATES = [
    "【限时优惠】{phrase}，速来抢购！",
    "品牌宣传：本产品获评{phrase}。",
    "标题：{phrase}的{product}，限时特惠。",
    "直播间口播：这款{product}{phrase}，家人们冲！",
    "商品详情页：{phrase}，值得信赖。",
    "门店海报：{phrase}。",
]
CASE_PRODUCTS = {
    "case-fanglinfu-2016": ["现炒板栗", "坚果礼盒"],
    "case-yisui-2024": ["氨基酸洗面奶", "双管洁面"],
    "case-wanya-2024": ["暑期编程课", "英语培训班"],
    "case-shangcheng-2024": ["肉丝拌川", "招牌拌面"],
    "case-samr-top10-2024": ["医美项目", "护肤精华"],
}

TRAIN_EXEMPTIONS = [
    "本品牌2023年度销售额在华东地区同品类中排名第一（数据来源：行业报告，广告主可提供证明）。",
    "本品荣获2023年度最佳设计奖（奖项名称含“最佳”，系依据国家有关规定评定）。",
    "推荐最佳使用期限：自生产之日起12个月内有效。",
    "本店销量第一（历史数据可查，广告主可提供证明）。",
    "依据国家标准GB/T 19001，本品为一级品（等级用语有标准依据）。",
    "本产品注册商标中含“最优”字样，此处仅用于指代商品名称。",
    "同一品牌自我比较：本代产品性能较上一代提升30%。",
    "使用时间建议：每日两次，每次最佳间隔8小时。",
    "这是本店最拿手的招牌菜，欢迎品尝（主观服务态度）。",
    "2023年双十一销量突破100万件（平台后台数据可查证）。",
    "公司成立于2015年（企业信息展示，不构成广告推销）。",
    "本产品获国家发明专利（专利名称中含“最佳”）。",
]
EVAL_EXEMPTIONS = [
    "依据地方标准DB33/T 1000，该产品为特级品。",
    "同一企业不同产品线自我比较：A系列较B系列更轻。",
    "依据国家有关规定评定的“最佳创新奖”称号。",
    "保存期限：最佳食用期为6个月。",
    "本店为连锁品牌直营店（企业信息展示）。",
]

COMPLIANT_TEMPLATES = [
    "本店商品支持七天无理由退换，售后有保障。",
    "新品上市：{product}，欢迎到店体验。",
    "工厂直发，品质稳定，物流快速。",
    "本产品采用可回收包装，环保健康。",
    "用户评价：包装完好，味道不错，物流很快。",
    "客服在线时间 9:00–21:00，欢迎咨询。",
    "下单后 48 小时内发货，全国包邮。",
    "本产品通过 ISO9001 质量管理体系认证。",
    "本店承诺所有食材当日采购、当日使用。",
    "会员积分可兑换指定礼品，活动规则详见门店公告。",
    "本产品适用于家庭日常清洁，请按说明使用。",
    "售后热线 400-000-0000，工作日 9:00–18:00 在线。",
]
TRAIN_COMPLIANT_INDICES = range(0, 9)
EVAL_COMPLIANT_INDICES = range(9, 12)

SEMANTIC_PARAPHRASES = [
    "无人能及",
    "天下无双",
    "冠绝群伦",
    "无可超越",
    "绝顶",
    "睥睨群雄",
    "独领风骚",
    "傲视同侪",
]


def _merge_detections(detections: tuple[Detection, ...]) -> list[Detection]:
    ordered = sorted(detections, key=lambda d: (d.family, d.span[0], -(d.span[1] - d.span[0])))
    merged: list[Detection] = []
    for detection in ordered:
        if merged and detection.family == merged[-1].family and detection.span[0] <= merged[-1].span[1]:
            if (detection.span[1] - detection.span[0]) > (merged[-1].span[1] - merged[-1].span[0]):
                merged[-1] = detection
            continue
        merged.append(detection)
    return merged


class RuleTeacher:
    """Deterministic labeller: M2 matcher + rule catalogue + exemption hint."""

    def __init__(self) -> None:
        self._matcher = Matcher()
        self._normalizer = Normalizer.from_data()

    def label(self, text: str) -> tuple[str, list[dict[str, Any]], list[str]]:
        detections = _merge_detections(self._matcher.match(text).detections)
        violations: list[dict[str, Any]] = []
        unmapped: list[str] = []
        for detection in detections:
            rule = TRULES.get(detection.family)
            if rule is None:
                unmapped.append(detection.family)
                continue
            violations.append(
                {
                    "rule_id": rule["rule_id"],
                    "clause_id": rule["clause_id"],
                    "article": rule["article"],
                    "item": rule["item"],
                    "span": [detection.span[0], detection.span[1]],
                    "evidence": detection.evidence,
                    "penalty_law_ref": rule["penalty_law_ref"],
                    "penalty_min": rule["penalty_min"],
                    "penalty_max": rule["penalty_max"],
                }
            )
        if EXEMPTION_HINT_RE.search(text):
            return "pass", [], unmapped
        if violations:
            return "block", violations, unmapped
        return "pass", [], unmapped


def _gold_json(decision: str, violations: list[dict[str, Any]]) -> str:
    return json.dumps({"decision": decision, "violations": violations}, ensure_ascii=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _term_fold(term: str) -> int:
    return int(_sha(term)[:2], 16) % 4


def _variant_positive_rows(teacher: RuleTeacher) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sample in generate_variants(seed=20261123):
        if sample.kind != "variant" or sample.expected_family not in TRULES:
            continue
        decision, violations, _ = teacher.label(sample.text)
        if decision != "block" or not violations:
            continue
        rows.append(
            {
                "kind": "violation",
                "source": "m2-variant",
                "variant_family": sample.variant_family,
                "family": sample.expected_family,
                "term": sample.term,
                "text": sample.text,
                "decision": "block",
                "violations": violations,
                "output": _gold_json("block", violations),
            }
        )
    return rows


FALSE_AD_TEMPLATES = [
    "【爆款推荐】{term}，买到就是赚到！",
    "品牌宣称：{term}，请放心购买。",
    "商品详情：{term}，限时秒杀。",
    "直播间口播：{term}，家人们别错过。",
    "广告语：{term}。",
]
FALSE_AD_TERMS = ["虚假广告", "虚假宣传", "引人误解", "虚构原价", "虚假标注", "名不副实", "言过其实"]


def _false_ad_rows(teacher: RuleTeacher) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for term in FALSE_AD_TERMS:
        for template in FALSE_AD_TEMPLATES:
            text = template.format(term=term)
            decision, violations, _ = teacher.label(text)
            if decision != "block" or not violations:
                continue
            rows.append(
                {
                    "kind": "violation",
                    "source": "false-ad-template",
                    "variant_family": "false_advertising",
                    "family": "false_advertising",
                    "term": term,
                    "text": text,
                    "decision": "block",
                    "violations": violations,
                    "output": _gold_json("block", violations),
                }
            )
    return rows


def _case_positive_rows(teacher: RuleTeacher) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payload = json.loads(SEEDS_PATH.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for case in payload["cases"]:
        covered = 0
        for phrase in case["phrases"]:
            if teacher.label(phrase)[0] != "block":
                coverage.append({"case": case["id"], "phrase": phrase, "status": "dictionary_gap"})
                continue
            covered += 1
            products = CASE_PRODUCTS.get(case["id"], ["商品"])
            for template_index, template in enumerate(CASE_CONTEXT_TEMPLATES):
                text = template.format(phrase=phrase, product=products[template_index % len(products)])
                decision, violations, _ = teacher.label(text)
                if decision != "block" or not violations:
                    continue
                rows.append(
                    {
                        "kind": "violation",
                        "source": f"case:{case['id']}",
                        "variant_family": "case_seed",
                        "family": violations[0]["rule_id"],
                        "term": phrase,
                        "text": text,
                        "decision": "block",
                        "violations": violations,
                        "output": _gold_json("block", violations),
                    }
                )
        coverage.append(
            {"case": case["id"], "covered_phrases": covered,
             "total_phrases": len(case["phrases"]), "source": case["source"]}
        )
    return rows, coverage


def _negative_rows(include_exemptions: bool, compliant_indices: range,
                   m2_indices: range) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    templates = list(NEGATIVE_TEMPLATES)
    for index in m2_indices:
        template = templates[index % len(templates)]
        rows.append(_compliant(f"方案{index + 1:03d}：{template}", "m2-negative"))
    products = ["坚果礼盒", "氨基酸洗面奶", "暑期课程", "拌川套餐", "护肤精华", "现炒板栗"]
    for index in compliant_indices:
        base = COMPLIANT_TEMPLATES[index]
        for product_index, product in enumerate(products):
            text = base.format(product=product) if "{product}" in base else base
            rows.append(_compliant(f"{text}（批次{index:02d}{product_index}）", "template-compliant"))
    if include_exemptions:
        for text in TRAIN_EXEMPTIONS:
            rows.append(_compliant(text, "guideline-exemption", kind="exemption"))
    return rows


def _compliant(text: str, source: str, kind: str = "compliant") -> dict[str, Any]:
    return {
        "kind": kind,
        "source": source,
        "variant_family": "exemption" if kind == "exemption" else "negative",
        "family": "",
        "term": "",
        "text": text,
        "decision": "pass",
        "violations": [],
        "output": _gold_json("pass", []),
    }


def _semantic_unknown_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for phrase in SEMANTIC_PARAPHRASES:
        for template in CASE_CONTEXT_TEMPLATES[:4]:
            text = template.format(phrase=phrase, product="产品")
            rows.append(
                {
                    "kind": "semantic_unknown",
                    "source": "semantic-paraphrase",
                    "variant_family": "semantic",
                    "family": "absolute_terms",
                    "term": phrase,
                    "text": text,
                    "decision": "block",
                    "violations": [],
                    "output": _gold_json("block", []),
                }
            )
    return rows


def _dpo_pairs(rows: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for row in rows:
        chosen = row["output"]
        if row["decision"] == "block":
            if rng.random() < 0.5:
                rejected = _gold_json("pass", [])
            else:
                other = TRULES["false_advertising" if row["violations"][0]["rule_id"].endswith("9-3")
                              else "absolute_terms"]
                rejected = _gold_json("block", [{
                    "rule_id": other["rule_id"], "clause_id": other["clause_id"],
                    "article": other["article"], "item": other["item"],
                    "span": row["violations"][0]["span"], "evidence": row["violations"][0]["evidence"],
                    "penalty_law_ref": other["penalty_law_ref"],
                    "penalty_min": other["penalty_min"], "penalty_max": other["penalty_max"],
                }])
        else:
            fake = TRULES["absolute_terms"]
            evidence = text_marker(row["text"])
            rejected = _gold_json("block", [{
                "rule_id": fake["rule_id"], "clause_id": fake["clause_id"],
                "article": fake["article"], "item": fake["item"],
                "span": [0, len(evidence)], "evidence": evidence,
                "penalty_law_ref": fake["penalty_law_ref"],
                "penalty_min": fake["penalty_min"], "penalty_max": fake["penalty_max"],
            }])
        pairs.append({"text": row["text"], "chosen": chosen, "rejected": rejected})
    return pairs


def text_marker(text: str) -> str:
    for marker in ("商品", "产品", "本店", "新品"):
        if marker in text:
            return marker
    return text[:2] if text else "商品"


def _assign_ids(rows: list[dict[str, Any]], prefix: str) -> None:
    for index, row in enumerate(rows):
        row["id"] = f"{prefix}-{index:05d}"
        row["text_sha256"] = _sha(row["text"])


def build(out_dir: Path) -> dict[str, Any]:
    teacher = RuleTeacher()
    rng = random.Random(20261123)

    variant_rows = _variant_positive_rows(teacher)
    case_rows, case_coverage = _case_positive_rows(teacher)

    train_rows: list[dict[str, Any]] = []
    eval_rows: list[dict[str, Any]] = []
    for row in variant_rows:
        (eval_rows if _term_fold(row["term"]) == EVAL_TERM_FOLD else train_rows).append(row)
    for row in _false_ad_rows(teacher):
        (eval_rows if _term_fold(row["term"]) == EVAL_TERM_FOLD else train_rows).append(row)
    for row in case_rows:
        (eval_rows if HOLDOUT_CASE in row["source"] else train_rows).append(row)

    train_negatives = _negative_rows(True, TRAIN_COMPLIANT_INDICES, range(0, 400))
    eval_negatives = _negative_rows(False, EVAL_COMPLIANT_INDICES, range(400, 540))
    eval_negatives.extend(_compliant(text, "guideline-exemption-heldout", kind="exemption")
                          for text in EVAL_EXEMPTIONS)

    train_rows.extend(train_negatives)
    eval_rows.extend(eval_negatives)

    # 10% of train rows -> validation (stratified by decision)
    rng.shuffle(train_rows)
    val_rows: list[dict[str, Any]] = []
    for decision in ("block", "pass"):
        pool = [row for row in train_rows if row["decision"] == decision]
        take = max(1, int(len(pool) * 0.1))
        val_rows.extend(pool[:take])
    train_rows = [row for row in train_rows if row not in val_rows]
    _assign_ids(train_rows, "train")
    _assign_ids(val_rows, "val")
    _assign_ids(eval_rows, "eval")
    unknown_rows = _semantic_unknown_rows()
    _assign_ids(unknown_rows, "unknown")

    seen = {row["text"] for row in train_rows + val_rows}
    leaked = [row["id"] for row in eval_rows if row["text"] in seen]
    if leaked:
        raise RuntimeError(f"eval/train leakage: {leaked[:5]}")

    dpo_pairs = _dpo_pairs(train_rows, rng)

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "version": "m4-data-v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "split_protocol": {
            "positives": f"term fold sha256(term)%4 == {EVAL_TERM_FOLD} -> eval; held-out case -> {HOLDOUT_CASE}",
            "negatives": "withheld compliant templates + 2 held-out exemption examples",
            "leakage_check": "zero text overlap between eval and train/val (build fails otherwise)",
        },
        "splits": {},
        "case_coverage": case_coverage,
        "semantic_unknown_phrases": SEMANTIC_PARAPHRASES,
        "teacher": "M2 matcher + rule catalogue + exemption hint (deterministic)",
    }

    def write(name: str, rows: list[dict[str, Any]]) -> None:
        payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
        path = out_dir / f"{name}.jsonl"
        path.write_text(payload, encoding="utf-8")
        manifest["splits"][name] = {
            "path": path.name,
            "rows": len(rows),
            "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "decisions": {
                "block": sum(1 for row in rows if row["decision"] == "block"),
                "pass": sum(1 for row in rows if row["decision"] == "pass"),
            },
            "kinds": sorted({row["kind"] for row in rows}),
        }

    write("sft_train", train_rows)
    write("sft_val", val_rows)
    write("eval", eval_rows)
    write("semantic_unknown", unknown_rows)

    dpo_payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in dpo_pairs) + "\n"
    (out_dir / "dpo_pairs.jsonl").write_text(dpo_payload, encoding="utf-8")
    manifest["splits"]["dpo_pairs"] = {
        "path": "dpo_pairs.jsonl",
        "rows": len(dpo_pairs),
        "sha256": hashlib.sha256(dpo_payload.encode("utf-8")).hexdigest(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "data_card.md").write_text(render_card(manifest), encoding="utf-8")
    return manifest


def render_card(manifest: dict[str, Any]) -> str:
    lines = [
        "# M4 数据卡（m4-data-v1）",
        "",
        f"> 生成时间：{manifest['generated_at']}；teacher：{manifest['teacher']}",
        "> 划分协议：",
    ]
    for key, value in manifest["split_protocol"].items():
        lines.append(f"> - {key}: {value}")
    lines += [
        "",
        "| 集合 | 行数 | block | pass | sha256 |",
        "|---|---:|---:|---:|---|",
    ]
    for name, info in manifest["splits"].items():
        decisions = info.get("decisions", {})
        lines.append(
            f"| {name} | {info['rows']} | {decisions.get('block', '-')} | {decisions.get('pass', '-')} | "
            f"{info['sha256'][:12]}… |"
        )
    lines += ["", "## 公开处罚案例种子覆盖", "", "| 案例 | 覆盖短语 | 来源 |", "|---|---:|---|"]
    for item in manifest["case_coverage"]:
        if "covered_phrases" in item:
            lines.append(f"| {item['case']} | {item['covered_phrases']}/{item['total_phrases']} | {item['source']} |")
    gaps = [item for item in manifest["case_coverage"] if item.get("status") == "dictionary_gap"]
    if gaps:
        lines += ["", "**词典缺口（规则教师无法覆盖，记录为规则升级项）**："]
        lines += [f"- `{gap['phrase']}`（案例 {gap['case']}）" for gap in gaps]
    lines += [
        "",
        "## 诚实说明",
        "",
        "- 违规标签来自确定性规则教师（M2 matcher + 规则库），不是人工标注；",
        "- 免责样本按 2023 年执法指南第五/六条标为 pass，**假设广告主可举证**；",
        "- 语义改写集（semantic_unknown）不参与训练、不进 M4 门禁，只用于报告覆盖差距；",
        "- eval 按词条分折 + 留出案例 + 留出模板/免责样本，构建时强制文本零重叠；",
        "- 规则教师覆盖不到的公开案例短语（如“最专业”“全国首家”）会列入词典缺口，"
        "说明规则升级（M5 热更新）与模型泛化都还有工作要做，不回避。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build M4 SFT/DPO/eval datasets")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    manifest = build(args.out)
    print(json.dumps({name: info["rows"] for name, info in manifest["splits"].items()},
                     ensure_ascii=False, indent=2))
    print(f"output: {args.out}")


if __name__ == "__main__":
    main()
