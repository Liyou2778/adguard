# AdGuard — 营销素材合规审核平台（P2）

> 把营销素材合规审核从「人看人判」变成「机器粗筛 + 条款级依据 + 人工只做复核」。
>
> 关联文档（上级目录）：
> [P2-PRD.md](../../P2-PRD.md) · [P2-技术设计.md](../../P2-技术设计.md) · [P2-交接文档.md](../../P2-交接文档.md)

## 当前状态

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **M0** | 环境就绪（Docker / GPU / JDK 21 / Go / Maven / Python） | **进行中** |
| M1 | 骨架：Go 网关 + Java 控制面 + compose 中间件 + CI | 未开始 |
| M2 | 法规结构化：规则模型、7 类变形归一化、条款解析器 | 未开始 |
| M3 | 知识工程：条款级切分、多路索引、rerank、增量索引、NDCG | 未开始 |
| M4 | 模型工程：标注集、SFT、DPO、蒸馏、门禁 | 未开始 |
| M5 | 推理与压测：vLLM / SGLang 对照、TTFT、成本 | 未开始 |
| M6 | 上线：K8s、规则灰度、模型灰度、回滚演练 | 未开始 |

## 技术栈

| 层 | 选型 | 语言 |
|---|---|---|
| 接入层 | 上传 / 分片 / 签名 URL / 限流 | Go |
| 控制面 | 多租户 / 规则库 / 审批 / 配额 / 审计 | Java 21 + Spring Boot 3 |
| 知识面 | 文档解析 / 条款级切分 / 混合检索 / rerank | Go（WeKnora 链路） |
| 模型面 | SFT / DPO / 蒸馏 / 评测 / vLLM·SGLang | Python 3.12 |

## 本地开发

```powershell
# 1) 每个新终端先执行（固定 JDK 21 + Maven 3.9.16）
. .\scripts\env.ps1

# 2) 环境自检（M0 门禁）
pwsh -File .\scripts\check-env.ps1
```

> 系统级 `java` 仍是 JDK 25（机器级 PATH 中的 JDK 25 优先于用户级 PATH，
> 改机器级 PATH 需要管理员权限）。项目内一律通过 `scripts\env.ps1`、
> Maven Wrapper 与 IDE 工程 SDK 锁定 JDK 21，详见 `docs/M0-环境就绪报告.md`。

## 目录规划（M1 起逐步落地）

```
services/
  gateway-go/          接入层（Go）
  control-plane-java/  控制面（Java / Spring Boot 3）
  knowledge/           知识工程（Go 服务 + Python 索引管线）
  model/               模型服务（Python：训练 / 评测 / 推理）
deploy/
  docker-compose.yml   本地全栈中间件
docs/
  adr/                 架构决策记录
scripts/               环境、自检、压测、部署脚本
```

## 完成定义（DoD）

CI 全绿 + 有测试 + 可演示 + README 更新 + ADR 记录 + TODO 登记。
每个训练实验必须有可复现的配置文件与指标记录（模型、数据版本、超参、结果）。
