# ADR-0004：条款级检索与规则判定的实现位置（WeKnora 关系）

- 状态：已接受（M3）
- 日期：2026-11-22
- 关联：PRD FR-4 / FR-7 / FR-8、ADR-2、ADR-3；P2-技术设计 §4（知识工程）、§5

## 背景

M3 要交付「条款级召回 + 混合检索 + rerank + 增量索引一致性 + 检索评测」。PRD 指定知识面底座为
腾讯 WeKnora（Go）。但本项目的文本语义层（M2）已经在 Python 服务里，且本机是
2C4G/15GB 的开发环境：

- WeKnora 完整部署需要 PostgreSQL + Elasticsearch + MinIO + Redis + docreader 等服务，
  还需要配置 LLM/Embedding 供应商（本项目要求私有化、不调公有云 API）；
- WeKnora 面向通用知识库（文档 → chunk → RAG），条款级结构化、法规树、罚则绑定仍要自研；
- 本阶段真正的验收点是**检索正确性**（条款级召回、revision 一致性、可评测），
  而不是"把 WeKnora 跑起来"。

## 决策

### 1. 不部署 WeKnora 进程，按它的检索/重排契约在 Python 内实现等价能力

复用 WeKnora 的**设计**与**不变式**，并在代码注释/ADR 中标注对应源码位置：

| WeKnora 能力 | 源码证据（本地缓存 commit `53ce747`） | 本项目等价实现 |
|---|---|---|
| 统一 rerank 入口 + 分数阈值 | `internal/reranking/rerank.go:111`（`Rerank`）、`:309`（`filterScores`） | `retrieval/rerank.py` 的 `Reranker` 协议 + 特征重排 |
| 混合检索 + 重排 | `internal/application/service/knowledgebase_search_rerank.go:64`（`HybridSearchWithRerank`）、`:157`（`rerankCandidates`） | `retrieval/service.py`：BM25 + 向量 + 规则路 + RRF + rerank |
| **chunk revision 一致性** | `knowledgebase_search_rerank_revision_test.go:11`（`TestRerankEnrichmentDropsChangedChunkRevision`） | 每条 clause 带 `revision=sha256(text)`；检索命中后与源侧 revision 比对，不一致即丢弃（`dropped_stale`） |
| rerank SSRF 防护 | `internal/models/rerank/transport.go:11`（`validateRerankBaseURL`） | M5 接 cross-encoder/TEI 时按同思路限制 baseURL 白名单 |

**接口保持可替换**：`RetrievalStore` 与 `Reranker` 都是协议/接口，M6 若要切换回
WeKnora（Go 服务 + ES/pgvector）或独立向量库，只需要换适配器，不改判定链路与 API。

### 2. 本地存储用 SQLite + sqlite-vec，而不是引入向量服务器

- 33 条条款规模下，KNN 用 `sqlite-vec` 的 `vec0` 虚拟表足够，且**零额外进程**（ECSM 2C4G 友好）；
- 条款元数据、revision、向量在同一个文件中，事务一致（写入/删除/检索原子）；
- 生产替换点明确：ES / pgvector / WeKnora 的存储实现同一 `RetrievalStore` 契约。

### 3. 多路召回 + RRF + 可解释特征重排

| 路 | 实现 | 解决什么 |
|---|---|---|
| 稀疏 | 字符二元组 BM25（自研，无分词依赖） | 中文条款精确词面召回 |
| 稠密 | `BAAI/bge-small-zh-v1.5`（512 维，CPU/onnx 不可用后用 transformers；HF 镜像下载） | 语义改写（"销量第一" → 绝对化用语条款） |
| 规则/家族 | 条款号精确匹配、M2 family 标签交集、条文角色（prohibition vs penalty） | 规则树命中；防止"罚则条款"被当成"禁止条款"引用 |

- 融合用 **RRF（k=60）**，不做跨路分数归一化（BM25 无界、余弦有界，混合会失真）；
- 重排用**可解释特征加权**（BM25 / dense / family / rule_exact / item_level / role_fit），
  权重放在 `data/retrieval/rerank_weights.json`（数据而非代码）；
- **意图过滤**：问"怎么罚"只查罚则条款，问"能不能写"只查禁止条款。
  M3 用关键词规则判定意图（可单测），M4 换成模型意图分类；
- cross-encoder 重排（bge-reranker / TEI）留到 M5：接口已抽象，直接替换实现。

### 4. 规则判定（M3 的端到端判据）

- `RuleJudge`（Python）= M2 matcher（family + span）+ M3 检索（clause + revision）+ M2 规则库
  （rule_id + 罚则）：三个来源缺一不可，**没有结构化规则/罚则的命中不下结论**，转人工；
- 只有带规则映射的 family（M3 为 absolute_terms→ART9-3、false_advertising→ART28）能产出 `block`；
- 免责语境（执法指南第五条/六条）在 M3 只做**提示词识别**，命中即转人工，不伪造豁免结论；
- Go 网关只做 I/O 与编排：读文本素材 → 调 `/v1/judge` → 落库；pipeline 不可用时降级人工，
  上传链路不受影响（继承 P1 的降级约定）。

### 5. 评测协议（防自嗨）

- 手写 material 查询集按奇偶切 dev/test：**权重只在 dev 上调，报告只报 test**；
- 自动生成的 clause 查询（词汇重叠高）只作 sanity，不作主指标；
- 未知语义改写不在词法承诺范围，归 M4；
- 检索指标：Recall@1/3/5、MRR、NDCG@10；门禁：material Recall@5 ≥0.90、NDCG@10 ≥0.75、
  clause Recall@5 ≥0.95。

## 后果

**正面**
- 条款级召回可量化（M3 报告），revision 一致性有测试与运行时守卫；
- 端到端可演示"违规文案 → block + 条款 + 罚则 + 原文 span"；
- 与 WeKnora 的对接点集中在两个协议，M6 可切换。

**负面 / 已知限制**
- 没有真正运行 WeKnora，因此"与 WeKnora 的行为一致性"只覆盖文档化不变式，不是逐行为对照；
- 33 条条款、CPU 向量模型，rerank topK/延迟结论不能直接外推到万级规则库（M5 重测）；
- penalty 意图靠关键词，复合问法会误判（M4 模型意图分类）；
- 条纹级规则库目前只有 2 条规则，其它 family 命中只能转人工（M4 扩充）。

## 被否方案

| 方案 | 否掉原因 |
|---|---|
| 部署完整 WeKnora 服务 | 资源/供应商配置成本高，验收点在检索正确性而非"跑起来" |
| 引入 Elasticsearch/向量服务器 | 2C4G 环境不划算；sqlite-vec 满足 M3 规模 |
| 直接用 BM25 单路 | 语义改写召回明显不足（material R@5 66.7% vs 混合+重排 92.6%） |
| 用大模型 API 做 rerank | 违反私有化约束 + 成本不可控 |
| 让网关直接实现检索/判定 | 文本语义会分裂在两种语言里，难以评测与迭代 |
