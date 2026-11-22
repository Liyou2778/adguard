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
| M2 | 法规结构化：规则模型、7 类变形归一化、条款解析器 | ⏳ 下一步 |
| M3 | 知识工程：条款级切分、多路索引、rerank、增量索引、NDCG | 未开始 |
| M4 | 模型工程：标注集、SFT、DPO、蒸馏、门禁 | 未开始 |
| M5 | 推理与压测：vLLM / SGLang 对照、TTFT、成本 | 未开始 |
| M6 | 上线：独立 ECS、规则灰度、模型灰度、回滚演练 | 规划完成 → [部署规划](docs/M6-独立ECS部署规划.md) |

## 技术栈

| 层 | 选型 | 语言 |
|---|---|---|
| 接入层 | 上传 / 分片 / 签名 URL / 限流 / 判定查询 | Go 1.27 |
| 控制面 | 多租户 / 规则库 / 版本 / 快照 / 审计 | Java 21 + Spring Boot 3.5 |
| 知识面 | 文档解析 / 条款级切分 / 混合检索 / rerank | Go + Python（WeKnora 链路，M3） |
| 模型面 | SFT / DPO / 蒸馏 / 评测 / vLLM·SGLang | Python 3.12（M4–M5） |

## 本地开发

```powershell
# 1) 每个新终端先执行（固定 JDK 21 + Maven 3.9.16）
. .\scripts\env.ps1            # 若执行策略拦截：powershell -ExecutionPolicy Bypass -File .\scripts\env.ps1

# 2) 环境自检（M0 门禁）
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-env.ps1

# 3) 起中间件（Redis / MinIO / MySQL）
powershell -ExecutionPolicy Bypass -File .\scripts\dev-up.ps1

# 4) 启动两个服务（各开一个终端）
powershell -ExecutionPolicy Bypass -File .\scripts\run-control-plane.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\run-gateway.ps1

# 5) M1 验收冒烟
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-m1.ps1
```

> 详细步骤、curl 手工链路、常见问题见 [M1 运行手册](docs/M1-运行手册.md)。
> 说明：系统级 `java` 仍是 JDK 25（机器级 PATH 优先），项目内一律通过 `scripts\env.ps1`、
> Maven Wrapper 与 IDE 工程 SDK 锁定 JDK 21，详见 [M0 报告](docs/M0-环境就绪报告.md)。

## 目录

```
deploy/                 # 本地 compose（M6 替换为 ECS + RDS + OSS）
services/
  gateway-go/           # Go 接入层：分片上传 / 去重 / 异步判定
  control-plane-java/   # Java 控制面：规则库 / 版本 / 快照
scripts/                # 环境、启动、冒烟、CI
docs/
  adr/                  # 架构决策记录
  api/                  # 接口契约
```

## 完成定义（DoD）

CI 全绿 + 有测试 + 可演示 + README 更新 + ADR 记录 + TODO 登记。
每个训练实验必须有可复现的配置文件与指标记录（模型、数据版本、超参、结果）。
