# AdGuard 合规流水线（Python）

> P2 交付：**7 类变形归一化 + 家族词典匹配（M2） + 条款级检索/rerank/规则判定（M3）**
> 定位：接入层（Go）只做 I/O 与编排；**文本归一化、检索、判定语义在 Python 侧**（`pypinyin` / `opencc` / embedding 生态只在 Python 成熟）。

## 模块

```
src/adguard_pipeline/
  normalization.py   归一化管线 + 原文 offset 映射（M2）
  ahocorasick.py     纯 Python AC 自动机（M2）
  dictionary.py      家族词典加载 + 变体模式生成（M2）
  matcher.py         归一化 + 多模式匹配 + 原文 span（M2）
  variants.py        7 类变形样本生成器（M2）
  dataset.py         变体评测集生成（1377 条）（M2）
  evaluation.py      变体命中率/误报评测（M2 门禁 ≥0.95）
  clause_parser.py   法规条文 → 条款树 + 结构化规则（M2）
  clause_eval.py     条款解析金标评测（M2 门禁 ≥0.90）
  api.py             FastAPI：normalize / match / retrieve / judge（M2–M3）
  retrieval/
    clause_source.py   条款来源：M2 JSON / Java 控制面
    bm25.py            字符二元组 BM25（稀疏路）
    embeddings.py      bge-small-zh-v1.5（稠密路）+ hashing fallback
    store.py           SQLite + sqlite-vec 索引（含 revision）
    fusion.py          RRF
    rerank.py          可解释特征重排
    service.py         多路召回 + rerank + revision 守卫
    queries.py         检索评测查询集
    evaluation.py      检索评测（Recall@K / NDCG@10 门禁）
    rule_judge.py      规则判定：matcher + 检索 + 规则库 → block/manual/pass
data/
  families/           家族词典（规则即数据）
  normalization/      归一化字符映射（异体字/拆字）
  regulations/        法规原文 fixture + 金标
  parsers/            解析输出（parsed_m2.json）
  retrieval/          检索评测查询 + 重排权重 + 指标
  datasets/           变体评测集
```

## 安装

```powershell
cd services\pipeline-py
python -m venv .venv

# 基础依赖
.\.venv\Scripts\python -m pip install -e ".[dev]" `
  -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

# M3 向量检索依赖（CPU wheel，避免拉 CUDA 版本）
.\.venv\Scripts\python -m pip install "torch==2.5.1" "transformers==4.46.3" "safetensors==0.4.5" `
  -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

# 向量模型（约 90MB，国内网络走 HF 镜像）
.\.venv\Scripts\python -m adguard_pipeline.retrieval.embeddings --download --endpoint https://hf-mirror.com
```

## 使用

```powershell
# M2 门禁
.\.venv\Scripts\python -m adguard_pipeline.dataset
.\.venv\Scripts\python -m adguard_pipeline.evaluation
.\.venv\Scripts\python -m adguard_pipeline.clause_parser
.\.venv\Scripts\python -m adguard_pipeline.clause_eval

# M3 门禁
.\.venv\Scripts\python -m adguard_pipeline.retrieval.evaluation --embedder bge

# 测试（76 个用例）
.\.venv\Scripts\python -m pytest

# 起服务（:8090）
.\.venv\Scripts\python -m uvicorn adguard_pipeline.api:app --port 8090
```

## 设计取舍

| 决策 | 选择 | 理由 |
|---|---|---|
| 归一化位置 | Python 服务 | pypinyin/opencc 生态在 Python；Go 侧重 I/O |
| AC 自动机 | 自研纯 Python | 无原生依赖、可单测、千级模式足够 |
| 拼音还原 | 模式侧展开（不重写文本） | 生成式还原不可逆，会破坏 offset |
| 向量存储 | SQLite + sqlite-vec | 单文件、零额外服务；接口可替换 WeKnora/ES |
| 融合 | RRF（k=60） | 免跨路分数归一化 |
| 重排 | 可解释特征加权 | 权重数据化可调；M5 换 cross-encoder |
| 规则判定 | 规则族 + 条款检索 + 罚则 | 无规则/罚则不下结论（不伪造） |

详见 `docs/adr/0003`（归一化/条款解析）与 `docs/adr/0004`（检索/判定/WeKnora 关系）。
