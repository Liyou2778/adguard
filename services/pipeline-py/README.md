# AdGuard 合规流水线（Python）

> P2 / M2 交付：**7 类变形归一化 + 家族词典匹配 + 变体测试集 + 条款解析器**
> 定位：接入层（Go）只做 I/O 与编排；**文本归一化、规则匹配、模型推理在 Python 侧**（`pypinyin` / `opencc` / 后续 embedding 生态只在 Python 成熟）。

## 为什么归一化要单独做成一个可评测的模块

关键词库追不上变体（PRD 痛点 P-1），而"追不上"这件事必须可量化，否则无法证明改进。
本模块把"归一化"变成一个**有输入、有输出、有指标**的纯函数管线：

```
原文 → NFKC → 剥离零宽/控制/emoji → 繁简统一 → 异体字/拆字映射 → 拼音回填 → AC 自动机粗筛
     → 命中列表（family / canonical / variant_kind / 原文 span）
```

- 每一步都**保留原文 offset 映射**，命中可以精确回指原文位置（FR-8 条款级定位的前提）；
- 词典是**数据文件**（`data/families/*.json`），新规只改数据不改代码（ADR-1）；
- 变体测试集与评测脚本在同一仓库，指标可复现（PRD M2 验收：命中率 ≥0.95）。

## 目录

```
src/adguard_pipeline/
  normalization.py   归一化管线 + offset 映射 + 审计轨迹
  ahocorasick.py     纯 Python Aho-Corasick（无原生依赖，可单测）
  dictionary.py      家族词典加载 + 变体模式生成（拼音/谐音/异体/拆字/同义）
  matcher.py         归一化 + 多模式匹配 + 原文 span 映射
  variants.py        变体样本生成器（7 类变形，用于评测集）
  dataset.py         评测集生成（≥1000 条，按族均衡）
  evaluation.py      指标计算（命中率/分族/误报）
  clause_parser.py   法规条文 → 结构化规则 JSON（M2）
  api.py             FastAPI：/v1/normalize /v1/match /v1/version
data/
  families/          家族词典（absolute_terms 等）
  datasets/          生成的评测集（JSONL，可复现）
  regulations/       法规原文 fixture
tests/               pytest
```

## 使用

```powershell
cd services\pipeline-py
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]" -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

# 生成评测集 + 跑指标（输出 Markdown 报告）
.\.venv\Scripts\python -m adguard_pipeline.dataset --write
.\.venv\Scripts\python -m adguard_pipeline.evaluation --report ..\..\docs\M2-变体归一化评测.md

# 起服务
.\.venv\Scripts\python -m uvicorn adguard_pipeline.api:app --port 8090

# 测试
.\.venv\Scripts\python -m pytest
```

## 设计取舍（M2 已定）

| 决策 | 选择 | 理由 |
|---|---|---|
| 归一化位置 | Python 服务 | pypinyin/opencc 生态在 Python；Go 侧重 I/O 并发 |
| AC 自动机 | 自研纯 Python | 无原生编译依赖（Windows 友好）、行为可单测、模式数 1000 级足够 |
| 拼音还原 | 词典反查（模式侧生成） | 生成式还原不可靠；把"词 → 拼音形态"预生成成模式，确定性可评测 |
| 同义/文言替换 | 家族同义词短语表 | R-2：只承诺已知变体族；未知改写交给 M4 语义模型，并在评测里单列 |
| 繁简统一 | 逐字 t2s（保持 1:1） | 短语级转换会改变长度、破坏 offset 映射 |
