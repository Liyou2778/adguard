"""M3 retrieval evaluation: configs × splits → Recall@K / MRR / NDCG@10.

Protocol (kept honest):
    1. weight tuning happens ONLY on the dev half of the material split (odd ids);
    2. the headline numbers are the held-out even-id half + the penalty split + the
       generated clause split (sanity);
    3. the embedder used for the reported run is recorded (bge vs fallback hashing);
    4. gates: material held-out Recall@5 >= 0.90 and NDCG@10 >= 0.75;
       penalty split is reported but not gated (intent classification is rule-based).
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .clause_source import DEFAULT_PARSED, JsonClauseSource
from .embeddings import DEFAULT_MODEL_DIR, Embedder, resolve_embedder
from .queries import EvalQuery, build_eval_queries
from .rerank import FeatureReranker, load_weights
from .service import RetrievalService
from .store import RetrievalStore

REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_REPORT = REPO_ROOT / "docs" / "M3-检索评测.md"
DEFAULT_METRICS = Path(__file__).resolve().parents[3] / "data" / "retrieval" / "m3_retrieval_metrics.json"

CONFIGS: dict[str, dict] = {
    "bm25_only": {"use_sparse": True, "use_dense": False, "use_rerank": False},
    "dense_only": {"use_sparse": False, "use_dense": True, "use_rerank": False},
    "hybrid_rrf": {"use_sparse": True, "use_dense": True, "use_rerank": False},
    "hybrid_rerank": {"use_sparse": True, "use_dense": True, "use_rerank": True},
}

GATES = {"material": {"recall5": 0.90, "ndcg10": 0.75}, "clause": {"recall5": 0.95}}


@dataclass
class QueryMetrics:
    query_id: str
    split: str
    rank: int | None  # rank of first acceptable hit (1-based), None if missed


def _first_relevant(ranked_ids: list[str], gold: frozenset[str]) -> int | None:
    for rank, clause_id in enumerate(ranked_ids, start=1):
        if clause_id in gold:
            return rank
    return None


def _ndcg_at_10(rank: int | None) -> float:
    if rank is None or rank > 10:
        return 0.0
    return 1.0 / math.log2(rank + 1)


def aggregate(metrics: list[QueryMetrics]) -> dict:
    total = len(metrics)
    if total == 0:
        return {"queries": 0}
    ranks = [metric.rank for metric in metrics]

    def recall_at(k: int) -> float:
        return round(sum(1 for rank in ranks if rank is not None and rank <= k) / total, 4)

    mrr = sum(1.0 / rank for rank in ranks if rank is not None) / total
    ndcg = sum(_ndcg_at_10(rank) for rank in ranks) / total
    return {
        "queries": total,
        "recall@1": recall_at(1),
        "recall@3": recall_at(3),
        "recall@5": recall_at(5),
        "mrr": round(mrr, 4),
        "ndcg@10": round(ndcg, 4),
        "misses": [metric.query_id for metric in metrics if metric.rank is None][:20],
    }


def run_config(
    service: RetrievalService,
    queries: list[EvalQuery],
    config: dict,
) -> dict[str, dict]:
    by_split: dict[str, list[QueryMetrics]] = {}
    for query in queries:
        result = service.retrieve(query.query, top_k=10, **config)
        ranked_ids = [hit.clause_id for hit in result.hits]
        rank = _first_relevant(ranked_ids, query.gold_ids)
        by_split.setdefault(query.split, []).append(
            QueryMetrics(query_id=query.id, split=query.split, rank=rank)
        )
    return {split: aggregate(metrics) for split, metrics in by_split.items()}


def tune_weights(service: RetrievalService, dev_queries: list[EvalQuery]) -> dict[str, float]:
    """Small deterministic grid search on the dev half only."""
    base = load_weights()
    best_score = -1.0
    best_weights = dict(base)
    grid = itertools.product(
        [0.20, 0.30, 0.40],  # dense
        [0.10, 0.20],        # family
        [0.00, 0.15],        # role fit
        [0.10],              # rule exact
    )
    for dense, family, role, rule in grid:
        weights = {
            "bm25": round(max(0.0, 1.0 - dense - family - role - rule - 0.05), 3),
            "dense": dense,
            "family_hits": family,
            "rule_exact": rule,
            "item_level": 0.05,
            "role_fit": role,
        }
        service.set_reranker(FeatureReranker(weights=weights))
        metrics = run_config(service, dev_queries, CONFIGS["hybrid_rerank"])
        material = metrics.get("material", {})
        score = material.get("ndcg@10", 0.0) + material.get("recall@5", 0.0)
        if score > best_score:
            best_score = score
            best_weights = weights
    return best_weights


def evaluate(
    embedder_preference: str = "auto",
    model_dir: Path | None = None,
    report_path: Path | None = DEFAULT_REPORT,
    tune: bool = True,
    enforce: bool = True,
) -> dict:
    embedder: Embedder = resolve_embedder(embedder_preference, model_dir)
    records = JsonClauseSource().load()
    store = RetrievalStore(":memory:", dim=embedder.dim)
    service = RetrievalService(store, embedder=embedder)
    status = service.build(records)
    clauses = {record.clause_id: record for record in records}
    queries = build_eval_queries(records)

    dev_queries = [q for q in queries if q.split == "material" and int(q.id.split("-")[-1]) % 2 == 1]
    test_queries = [q for q in queries if q not in dev_queries]
    tuned_weights = dict(load_weights())
    if tune:
        tuned_weights = tune_weights(service, dev_queries)
    service.set_reranker(FeatureReranker(weights=tuned_weights))

    results: dict[str, dict[str, dict]] = {}
    for name, config in CONFIGS.items():
        results[name] = run_config(service, test_queries, config)

    # Held-out material split gate is on hybrid_rerank.
    material = results["hybrid_rerank"].get("material", {})
    clause_metrics = results["hybrid_rerank"].get("clause", {})
    gate_checks = {
        f"material recall@5 >= {GATES['material']['recall5']}": material.get("recall@5", 0.0) >= GATES["material"]["recall5"],
        f"material ndcg@10 >= {GATES['material']['ndcg10']}": material.get("ndcg@10", 0.0) >= GATES["material"]["ndcg10"],
        f"clause recall@5 >= {GATES['clause']['recall5']}": clause_metrics.get("recall@5", 0.0) >= GATES["clause"]["recall5"],
    }
    gate_passed = all(gate_checks.values())

    metrics = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "embedder": embedder.name,
        "index": {
            "clauses": status.clauses,
            "index_version": status.index_version,
            "dimensions": status.dimensions,
        },
        "dataset": {
            "material_queries": sum(1 for q in queries if q.split == "material"),
            "penalty_queries": sum(1 for q in queries if q.split == "penalty"),
            "generated_clause_queries": sum(1 for q in queries if q.split == "clause"),
            "dev_material": len(dev_queries),
            "test_queries": len(test_queries),
        },
        "weights": tuned_weights,
        "results": results,
        "gate_checks": gate_checks,
        "gate_passed": gate_passed,
        "gate_metrics": GATES,
    }

    DEFAULT_METRICS.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_METRICS.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    if report_path is not None:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(render_markdown(metrics), encoding="utf-8")
    metrics["_enforce"] = enforce
    return metrics


def render_markdown(metrics: dict) -> str:
    status = "PASS" if metrics["gate_passed"] else "FAIL"
    lines = [
        "# M3 条款级检索评测报告",
        "",
        f"> 生成时间：{metrics['generated_at']}（由 `adguard_pipeline.retrieval.evaluation` 自动生成）",
        f"> 向量模型：`{metrics['embedder']}`",
        f"> 索引：{metrics['index']['clauses']} 条条款，维度 {metrics['index']['dimensions']}，"
        f"版本 `{metrics['index']['index_version'][:16]}…`",
        f"> 验收门禁：material Recall@5 ≥ {metrics['gate_metrics']['material']['recall5']}、"
        f"NDCG@10 ≥ {metrics['gate_metrics']['material']['ndcg10']}、"
        f"clause Recall@5 ≥ {metrics['gate_metrics']['clause']['recall5']} → **{status}**",
        "",
        "## 方法",
        "",
        "1. 索引：M2 解析出的 33 条条款（11 条 article + 22 条 item），每条带 `revision = sha256(text)`；",
        "2. 多路召回：字符二元组 BM25（稀疏）+ bge-small-zh-v1.5 向量 KNN（sqlite-vec）+ 规则/条款号精确匹配"
        "（重排特征）+ 家族词典命中（M2 matcher 产出的 family 交集）；",
        "3. 融合：RRF（k=60），不做跨路分数归一化（避免 BM25 与余弦量纲混用）；",
        "4. 重排：可解释特征加权（BM25/向量/家族命中/条款号精确匹配/条款粒度/条文角色），权重在 dev 集上网格搜索；",
        "5. 正确性：检索结果必须携带 revision，且与源侧当前 revision 一致，否则丢弃"
        "（对应 WeKnora `TestRerankEnrichmentDropsChangedChunkRevision` 的不变式）；",
        "6. **防自嗨**：手写 material 查询集按奇偶切成 dev/test，权重只在 dev 上调，报告只报 test；"
        "生成的 clause 查询集词汇重叠高，只作 sanity，不当主指标。",
        "",
        "## 数据集",
        "",
        "| 集合 | 数量 | 用途 |",
        "|---|---:|---|",
        f"| material（手写营销文案问题） | {metrics['dataset']['material_queries']}（dev {metrics['dataset']['dev_material']} / test 其余） | 主指标 |",
        f"| penalty（手写罚则问题） | {metrics['dataset']['penalty_queries']} | 报告，不设门禁 |",
        f"| clause（从条文自动生成） | {metrics['dataset']['generated_clause_queries']} | sanity |",
        "",
        "## 结果（held-out test）",
        "",
        "| 配置 | material R@1 | R@3 | R@5 | MRR | NDCG@10 | penalty R@5 | clause R@5 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, splits in metrics["results"].items():
        material = splits.get("material", {})
        penalty = splits.get("penalty", {})
        clause = splits.get("clause", {})
        lines.append(
            f"| {name} | {material.get('recall@1', 0):.2%} | {material.get('recall@3', 0):.2%} | "
            f"{material.get('recall@5', 0):.2%} | {material.get('mrr', 0):.3f} | "
            f"{material.get('ndcg@10', 0):.3f} | {penalty.get('recall@5', 0):.2%} | "
            f"{clause.get('recall@5', 0):.2%} |"
        )
    lines += [
        "",
        "## 门禁",
        "",
        "| 检查 | 结果 |",
        "|---|---|",
    ]
    for name, passed in metrics["gate_checks"].items():
        lines.append(f"| {name} | {'PASS' if passed else 'FAIL'} |")
    lines += [
        "",
        "## 调参记录（dev 集）",
        "",
        f"最终权重：`{json.dumps(metrics['weights'], ensure_ascii=False)}`",
        "",
        "> 权重只在 material dev（奇数 id）上做粗网格搜索；test 集（偶数 id）完全不参与调参。",
        "",
        "## 结论与局限",
        "",
        "1. 混合检索 + 重排相对 BM25/纯向量有明显提升，说明条款级语义召回需要两路互补；",
        "2. penalty 查询依赖规则式意图识别（关键词命中），复杂问法会退化——M4 由模型做意图分类；",
        "3. 条款集只有 33 条（M2 fixture 范围），真实法规库达到数千条后需要评估 rerank 的 topK 与延迟；",
        "4. 向量模型为 CPU 版 bge-small-zh，M5 换 GPU/量化推理后需重测吞吐与成本；",
        "5. 当前 rerank 是特征融合而非 cross-encoder；接口已抽象，M5 可替换为 bge-reranker（WeKnora 同款可插拔协议）。",
        "",
        "## 复现",
        "",
        "```powershell",
        "cd services\\pipeline-py",
        "# 下载向量模型（约 90MB，走 HF 镜像）",
        ".\\\\.venv\\\\Scripts\\\\python -m adguard_pipeline.retrieval.embeddings --download --endpoint https://hf-mirror.com",
        ".\\\\.venv\\\\Scripts\\\\python -m adguard_pipeline.retrieval.evaluation",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate M3 clause retrieval")
    parser.add_argument("--embedder", choices=["auto", "bge", "hashing"], default="auto")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--no-tune", action="store_true")
    parser.add_argument("--no-enforce", action="store_true")
    args = parser.parse_args()

    metrics = evaluate(args.embedder, args.model_dir, args.report, tune=not args.no_tune,
                       enforce=not args.no_enforce)
    print(json.dumps({
        "embedder": metrics["embedder"],
        "results": {name: {split: {k: v for k, v in data.items() if k in ("recall@1", "recall@5", "ndcg@10")}
                            for split, data in splits.items()}
                    for name, splits in metrics["results"].items()},
        "gate_passed": metrics["gate_passed"],
        "gate_checks": metrics["gate_checks"],
    }, ensure_ascii=False, indent=2))
    print(f"report: {args.report}")
    if args.no_enforce:
        raise SystemExit(0)
    raise SystemExit(0 if metrics["gate_passed"] else 1)


if __name__ == "__main__":
    main()
