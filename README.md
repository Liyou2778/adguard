# AdGuard — 营销素材合规审核平台（P2）

> 把营销素材合规审核从「人看人判」变成「机器粗筛 + 条款级依据 + 人工只做复核」。
>
> 关联文档（上级目录）：
> [P2-PRD.md](../../P2-PRD.md) · [P2-技术设计.md](../../P2-技术设计.md) · [P2-交接文档.md](../../P2-交接文档.md)

## 当前状态

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **M0** | 环境就绪（Docker / GPU / JDK 21 / Go / Maven / Python） | ✅ 2026-11-22 通过（7/7）→ [报告](docs/M0-环境就绪报告.md) |
| **M1** | 骨架：Go 网关 + Java 控制面 + compose 中间件 + CI | ✅ 2026-11-22 通过（冒烟 29/29）→ [验收报告](docs/M1-验收报告.md) |
| **M2** | 法规结构化：规则模型、7 类变形归一化、条款解析器 | ✅ 2026-11-22 通过（变体 1377/1377、条款 38/38）→ [验收报告](docs/M2-验收报告.md) |
| **M3** | 知识工程：条款级多路检索、rerank、增量索引一致性、规则判定 | ✅ 2026-11-22 通过（material R@5 92.6%、冒烟 21/21）→ [验收报告](docs/M3-验收报告.md) |
| **M4** | 模型工程：标注集、SFT、DPO、蒸馏、门禁 | ✅ SFT 门禁全过（recall 0.9973 / 误杀 0 / 错引 0 / JSON 100%）；DPO 负结果已归档；蒸馏链路 0.5B 验证 → [验收报告](docs/M4-验收报告.md) |
| M3 | 知识工程：条款级切分、多路索引、rerank、增量索引、NDCG | 未开始 |
| M4 | 模型工程：标注集、SFT、DPO、蒸馏、门禁 | 未开始 |
| M5 | 推理与压测：vLLM / SGLang 对照、TTFT、成本 | 未开始 |
| M6 | 上线：独立 ECS、规则灰度、模型灰度、回滚演练 | 规划完成 → [部署规划](docs/M6-独立ECS部署规划.md) |

## 技术栈

| 层 | 选型 | 语言 |
|---|---|---|
| 接入层 | 上传 / 分片 / 签名 URL / 限流 / 判定查询 | Go 1.27 |
| 控制面 | 多租户 / 规则库 / 版本 / 快照 / 法规导入 / 审计 | Java 21 + Spring Boot 3.5 |
| 知识面 | 归一化 / 变形对抗 / 条款解析 / 混合检索 / rerank / 规则判定 | Python 3.12（M2–M3 已交付） |
| 模型面 | SFT / DPO / 蒸馏 / 评测 / vLLM·SGLang | Python（M4–M5） |

## 本地开发

```powershell
# 1) 每个新终端先执行（固定 JDK 21 + Maven 3.9.16）
. .\scripts\env.ps1            # 若执行策略拦截：powershell -ExecutionPolicy Bypass -File .\scripts\env.ps1

# 2) 环境自检（M0 门禁）
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-env.ps1

# 3) 起中间件（Redis / MinIO / MySQL）
powershell -ExecutionPolicy Bypass -File .\scripts\dev-up.ps1

# 4) 启动三个服务（各开一个终端）
powershell -ExecutionPolicy Bypass -File .\scripts\run-control-plane.ps1
# 流水线服务（M2 归一化/匹配 + M3 检索/判定）
powershell -ExecutionPolicy Bypass -File .\scripts\run-pipeline.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\run-gateway.ps1

# 5) 验收冒烟
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-m1.ps1   # 上传→判定闭环（回归）
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-m3.ps1   # 条款级规则判定（M3）
```

> 详细步骤、curl 手工链路、常见问题见 [M1 运行手册](docs/M1-运行手册.md)。
> 说明：系统级 `java` 仍是 JDK 25（机器级 PATH 优先），项目内一律通过 `scripts\env.ps1`、
> Maven Wrapper 与 IDE 工程 SDK 锁定 JDK 21，详见 [M0 报告](docs/M0-环境就绪报告.md)。

## 目录

```
deploy/                 # 本地 compose（M6 替换为 ECS + RDS + OSS）
services/
  gateway-go/           # Go 接入层：分片上传 / 去重 / 异步判定（规则判定客户端）
  control-plane-java/   # Java 控制面：规则库 / 版本 / 快照 / 法规导入
  pipeline-py/          # Python 知识面：归一化 / 变形对抗 / 条款解析 / 检索 / 规则判定
scripts/                # 环境、启动、冒烟、CI
docs/
  adr/                  # 架构决策记录（0001–0004）
  api/                  # 接口契约（m1/m2/m3）
```

## M2/M3 流水线（快速使用）

```powershell
cd services\pipeline-py
.\.venv\Scripts\python -m pytest                                    # 76 个测试用例
.\.venv\Scripts\python -m adguard_pipeline.retrieval.evaluation --embedder bge   # M3 检索门禁
.\.venv\Scripts\python -m uvicorn adguard_pipeline.api:app --port 8090
```

条款级证据示例（网关判定原文 → `/v1/judge`）：

```json
{"decision":"block",
 "violations":[{"rule_id":"ADV-LAW-ART9-3","clause_id":"ADV-LAW-ART9-ITEM3",
                "article":"第九条","item_label":"（三）","penalty_law_ref":"第五十七条",
                "penalty_min":200000,"penalty_max":1000000,
                "span":[0,4],"evidence":"全网最低","clause_revision":"f2954377…"}]}
```

## 完成定义（DoD）

CI 全绿 + 有测试 + 可演示 + README 更新 + ADR 记录 + TODO 登记。
每个训练实验必须有可复现的配置文件与指标记录（模型、数据版本、超参、结果）。
## 身份、申诉与账单（M8–M10）

```bash
# 管理员登录（首次启动由 BootstrapAdminRunner 创建，密码来自
# ADGUARD_BOOTSTRAP_ADMIN_PASSWORD 或一次性打印在日志）
TOKEN=$(curl -s -X POST localhost:8081/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<bootstrap-password>"}' | jq -r .access_token)

# 管理台（单页）：打开 http://<host>:8081/admin-ui/
# 商家申诉：Authorization: Bearer <tenant-user-token>
# 复核：PATCH /admin/appeals/{id}/review  -> 自动生成 feedback_pair（DPO 回流）
# 账单：GET /admin/billing/summary?month=YYYY-MM
#       GET /admin/billing/export.csv / export.pdf
```

生产鉴权环境变量（写入 `/opt/adguard/config/*.env`，不提交 git）：
`ADGUARD_JWT_SECRET`、`ADGUARD_ADMIN_KEY`、`ADGUARD_AUTH_REQUIRED=true`（控制面）；
`GATEWAY_JWT_SECRET`、`GATEWAY_API_KEY`、`GATEWAY_AUTH_REQUIRED=true`、`GATEWAY_QUOTA_ENABLED=true`（网关）。
