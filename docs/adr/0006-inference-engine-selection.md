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

## 实测结果（2026-11-24/25，AutoDL RTX 4090 48GB）

| 模型 | 并发 | req/s | TTFT p50 | TPOT p50 | 错误 |
|---|---:|---:|---:|---:|---:|
| 7B-SFT fp16 | c=1 | 0.569 | 45 ms | 16.2 ms | 0 |
| 7B-SFT fp16 | c=8 | 4.388 | 35.6 ms | 18.0 ms | 0 |
| 7B-SFT fp16 | c=32 | **13.893** | 58.0 ms | 23.3 ms | 0 |
| 本地 3B-SFT fp16（8GB 卡） | c=8 | 2.362 | 71 ms | 35 ms | 0 |

- 7B-SFT 服务端 535 条门禁：recall 1.0000 / 误杀 1.23% / 错引 0.80%（3/374）/ JSON 100%（3/4 门禁，错引未达"==0"）；
- 7B-DPO 负结果：JSON 88.41%、recall 86.56%（输出啰嗦被 `max_tokens=384` 截断），维持 R-4 回退用 SFT；
- 单位成本（按 AutoDL ¥2.18/h 折算）：c=32 时 **≈¥0.044/千次判定**；c=1 时 ≈¥1.06/千次；
- SGLang 对比：安装/压测进行中（timebox 25 分钟），结果补入本 ADR 后定稿。

## 决策

1. **生产推理引擎采用 vLLM**：OpenAI 兼容接口、PagedAttention/continuous batching、量化与运维生态最成熟；7B-SFT 在单卡 4090 上 13.89 req/s 已满足单租户需求，且模型侧成本仅 ¥0.04/千次；
2. **部署形态**：M6 的 2C4G ECS 只跑网关 + 规则判定（CPU）；GPU 推理作为独立按量服务，通过 `ADGUARD_MODEL_JUDGE_URL` 接入，模型不可用时自动降级为纯规则；
3. **流量分配（降本）**：规则 `block`/`pass` 直接返回，不调模型；只有 `manual`（含语义漏检升级）才调 GPU 服务 —— 按 535 条实测分布，仅 27/535 ≈ 5% 流量需要模型，GPU 成本再降一个量级；
4. **质量门禁**：模型输出必须过 `validate_verdict`（引用条款必须存在于本地规则库对应 family），错引条款在 M6 前修复并复测到 0；
5. SGLang 若无法在本类环境稳定运行，不阻塞上线（vLLM 为主选型），结果记录在 M5 报告。

## 状态

- [x] 压测客户端与启动脚本
- [x] vLLM 安装与本地验证（0.5B / 3B）
- [x] 本地 535 条服务端门禁（3B，3/4）
- [x] 混合判定（规则+模型）535 条同栈对比（0 规则降级 / 1 语义升级 / 2 豁免确认）
- [x] 云端 7B SFT + DPO + 合并 + vLLM 压测（1/4/8/16/32）+ 535 条门禁
- [x] 成本折算（¥0.044/千次 @c=32）
- [ ] SGLang 对比结果（安装/压测进行中）→ 补入后本 ADR 定稿
