# M3 接口契约（v0.3）：条款检索 + 规则判定

> 在 M1/M2 契约基础上的增量。
> Python 合规服务默认 `http://127.0.0.1:8090`（归一化 + 检索 + 规则判定）。

## 1. 条款检索

### 1.1 检索

`POST /v1/retrieve`

```json
{"query": "全网最低价，国家级认证", "top_k": 5, "use_dense": true, "use_rerank": true}
```

响应 `200`：

```json
{
  "query": "全网最低价，国家级认证",
  "index_version": "cf6058e5…（sha256）",
  "dropped_stale": 0,
  "warnings": [],
  "embedder": "BAAI/bge-small-zh-v1.5",
  "hits": [
    {
      "clause_id": "ADV-LAW-ART9-ITEM3",
      "revision": "f2954377…（sha256，条款内容哈希）",
      "score": 0.87,
      "bm25_score": 3.42,
      "dense_score": 0.81,
      "rerank_score": 0.87,
      "family_hits": 1,
      "text": "使用“国家级”、“最高级”、“最佳”等用语；",
      "article": "第九条",
      "item_label": "（三）",
      "regulation_id": "ADV-LAW",
      "law": "中华人民共和国广告法",
      "families": ["absolute_terms"]
    }
  ]
}
```

- **revision 守卫**：`revision` 是索引时的条款内容哈希；若源侧条款已变更，命中会被丢弃并计入
  `dropped_stale`，`warnings` 给出说明（对应 WeKnora `TestRerankEnrichmentDropsChangedChunkRevision`）；
- 查询意图为罚则（含"罚/处罚/罚款/责任/吊销"等）时只返回罚则类条款；否则只返回禁止类条款。

### 1.2 索引状态 / 重建

- `GET /v1/index/status` → `{"clauses":33,"index_version":"…","embedder":"…","dimensions":512,"source":"json"}`
- `POST /v1/index/rebuild` → 从当前 clause source（JSON 或控制面）重建索引

## 2. 规则判定

`POST /v1/judge`

```json
{"text": "全网最低价，错过再等一年！本店承诺国家级品质。", "tenant_id": "demo-platform"}
```

响应 `200`：

```json
{
  "decision": "block",
  "reason": "命中 2 处条款级规则违规（M3 规则判定；未使用语义模型）",
  "judge_version": "rule-m3-v1",
  "retrieval_warnings": [],
  "dropped_stale": 0,
  "index_version": "cf6058e5…",
  "violations": [
    {
      "rule_id": "ADV-LAW-ART9-3",
      "family": "absolute_terms",
      "canonical": "全网最低",
      "variant_kind": "canonical",
      "span": [0, 4],
      "evidence": "全网最低",
      "clause_id": "ADV-LAW-ART9-ITEM3",
      "regulation_id": "ADV-LAW",
      "article": "第九条",
      "item_label": "（三）",
      "clause_revision": "f2954377…",
      "clause_text": "使用“国家级”、“最高级”、“最佳”等用语；",
      "penalty_law_ref": "第五十七条",
      "penalty_min": 200000,
      "penalty_max": 1000000,
      "confidence": 0.95
    }
  ]
}
```

判定语义：

| decision | 条件 |
|---|---|
| `block` | 命中已知 family 且**检索到带结构化规则（rule_id + 罚则）的条款**，span 可回指原文 |
| `manual` | 命中 family 但无规则/罚则可引用；或文本含免责语境线索；或 pipeline 不可用（降级） |
| `pass` | 未命中任何已知 family（M3 无语义模型，未知改写会漏过，M4 补齐） |

## 3. 网关行为（Go）

- 新配置：`GATEWAY_JUDGE_BACKEND=rule|stub`（默认 rule）、`GATEWAY_PIPELINE_URL`（默认 `http://127.0.0.1:8090`）、
  `GATEWAY_PIPELINE_TIMEOUT`（默认 10s）；
- 仅 `type=text` 素材走规则判定；图片/视频在 OCR 实现前一律 `manual`（M4 多模态）；
- pipeline 超时/报错 → `manual` 降级，上传与查询链路不受影响；
- 判定结果落库新增 violation 字段（V3 迁移）：`family/canonical/variant_kind/clause_id/regulation_id/article/
  item_label/clause_revision/clause_text/penalty_law_ref/penalty_min/penalty_max`；
- `GET /api/v1/judgments/{id}` 返回上述 clause 级证据（FR-8 条款级定位）。
