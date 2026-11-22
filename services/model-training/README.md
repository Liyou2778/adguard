# AdGuard 模型工程（M4）

> P2 / M4：标注数据构建 → SFT（本地 3B QLoRA）→ DPO → 蒸馏 → 门禁评测
> 训练在 **WSL2 Ubuntu + CUDA** 中进行（Windows 侧只做数据构建与评测调度），详见 `docs/M4-配置与训练手册.md`。

## 目标（PRD M4 门禁，不降标）

| 指标 | 门禁 |
|---|---|
| 违规召回 recall | ≥ 0.95 |
| 误杀 false positive rate | ≤ 0.03 |
| 条款命中（clause hit，预测出的每个 violation 的 clause_id 都必须正确） | 100% |
| 训练可复现 | 每次实验有 config + 数据版本 + 指标记录 |

## 目录

```
configs/              实验配置（sft / dpo / distill）
data/
  seeds/              公开行政处罚案例种子（短引用 + 出处）
  generated/          构建出的训练/评测数据（JSONL，提交以便复现）
src/adguard_training/
  schema.py           模型输出 JSON schema + 校验
  prompts.py          system/user 模板（含条款目录）
  build_dataset.py    规则教师标注 → SFT/DPO/eval 数据
  evaluate.py         模型评测（recall / FPR / clause hit / JSON 解析率）
  train_sft.py        QLoRA SFT（trl + peft + bitsandbytes）
  train_dpo.py        DPO（偏好对来自规则教师 + 合成反例）
  distill.py          教师→学生蒸馏（M4 本地小规模验证，M5 上云放大）
  registry.py         实验登记（config/data hash/metrics）
reports/              实验指标与说明（提交）
```

## 为什么用"规则教师"造标签

PRD R-3 允许「规则 + 教师模型合成 + 人工抽检关键集」。M4 的教师是 **M2 matcher + M3 条款检索 + 规则库**
（确定性、可复现、可审计），标签直接来自 `RuleJudge`。它的短板（语义改写、语境豁免）正是模型要学的部分，
因此评测集单独包含"规则漏检的语义改写"和"规则误杀的豁免语境"两类样本，如实报告。

## 快速开始

```bash
# WSL 环境一次性准备（见 docs/M4-配置与训练手册.md）
bash scripts/setup_wsl_train.sh

# 数据构建（Windows 侧，用 pipeline-py 的 venv）
cd services/pipeline-py
.\.venv\Scripts\python -m adguard_training.build_dataset --out ..\model-training\data\generated

# SFT（WSL）
cd /root/adguard-train
.venv/bin/python -m adguard_training.train_sft --config configs/sft_qwen25_3b_qlora.json

# 评测（WSL）
.venv/bin/python -m adguard_training.evaluate --adapter runs/sft/checkpoint-xxx --split eval
```
