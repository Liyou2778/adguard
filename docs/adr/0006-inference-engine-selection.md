# ADR-0006：推理引擎选型（vLLM vs SGLang）与部署形态

- 状态：草案（M5 实测后定稿）
- 日期：2026-11-23
- 关联：PRD M5/M6、ADR-0005；实测计划见 `docs/M5-推理压测计划.md`

## 背景

M4 产出 QLoRA adapter（训练形态），M5 要回答三个问题：

1. 3B/7B 模型在什么推理引擎上跑、能承受多少 QPS、P99 多少；
2. 每次判定的单位成本（元/千次），与 M3 规则判定（CPU ~50–150ms/次）相比是否值得；
3. M6 的 ECS(2C4G) 与 GPU 服务如何分工。

## 候选与约束

| 引擎 | 优势 | 风险/成本 |
|---|---|---|
| vLLM | PagedAttention、continuous batching、OpenAI 兼容、生态成熟、AWQ/GPTQ 量化支持 | 7B 需 ≥24GB（A10/4090） |
| SGLang | RadixAttention 对**共享 system prompt** 前缀友好（本项目每次请求都带同一规则目录，命中率高） | 安装依赖多（flashinfer/sgl-kernel），WSL/消费卡兼容性需验证 |
| 本地 8GB | RTX 4060 只能跑 0.5B/1.5B fp16 或 3B 小 context | 只做链路验证，不代表生产容量 |

## 计划中的验证路径（已写入 M5 计划）

1. 本地 8GB：vLLM 起 0.5B student（或 merged 3B + `max-model-len=1024`、`gpu-memory-utilization=0.85`），
   跑通 OpenAI API + 压测脚本（TTFT/TPOT/吞吐/错误率）；
2. 云 GPU（A10 24GB 按量）：merged 3B fp16 与 7B-Instruct 对照，vLLM vs SGLang 同输入集压测；
3. 量化：3B merge → AWQ 4bit，对比 fp16 的吞吐/质量（M4 eval 集子集）；
4. 成本：实例时价 ÷ 实测 QPS → 元/千次判定；与规则判定对比，给出"规则优先、模型兜底"的流量分配建议。

## 选型判据（数据说话，不凭社区口碑）

- 若 SGLang 在共享前缀下 TTFT 明显低于 vLLM（≥20%）且稳定，选 SGLang；
- 若差距 <10%，选 vLLM（生态、量化、运维成熟度优先）；
- 无论选哪个，M6 的 2C4G ECS 只跑规则判定 + 网关；模型推理走 GPU 服务（按量），
  网关按"规则命中即 block；规则转人工且 GPU 在线时才调模型"的策略降本。

## 状态

- [x] 压测客户端与启动脚本（M4 期间完成）
- [x] vLLM 0.6.3 独立 venv 安装（WSL，torch 2.4.0+cu121）
- [x] 本地链路实测：0.5B 348 req 0 error；3B 合并模型 vLLM 可服务（c=8 2.36 req/s、TTFT 71ms）；服务端 535 条 gate 3/4
- [x] 混合判定（规则+模型）端到端验证通过（6 个代表性案例，含免责确认与语义漏检升级人工）
- [ ] 云 GPU（A10 24GB）正式压测 + 成本折算 → 定稿本 ADR + `docs/M5-推理压测报告.md`
