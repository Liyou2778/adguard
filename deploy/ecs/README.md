# P2 ECS 部署编排（M6）

> 目标：2C4G Alinux3 单机部署 Go 网关 + Java 控制面 + Python 流水线 + Redis，MySQL 走 RDS、对象存储走 OSS。
> 灰度方式：nginx `split_clients` 按租户+IP 把流量分到 stable(:18080) / canary(:28080)，逐级 10% → 50% → 100%，出问题一键回滚。

## 文件

| 文件 | 用途 |
|---|---|
| `setup-ecs.sh` | ECS 基线：目录/用户、docker+redis、nginx、JDK、日志轮转、sysctl |
| `adguard-gateway-stable.service` | stable 网关（:18080，`/opt/adguard/current`） |
| `adguard-gateway-canary.service` | canary 网关（:28080，`/opt/adguard/canary`） |
| `adguard-control-plane.service` | Java 控制面（:8081，JVM 1G 堆） |
| `adguard-pipeline.service` | Python 流水线（:8090，内存上限 1.5G） |
| `config/*.env.example` | 环境变量模板（真实值放 `/opt/adguard/config/*.env`，chmod 600） |
| `switch-canary.sh` | 切换灰度比例 0/10/50/100 + 健康检查 |
| `remote-deploy-gateway.sh` | 服务器侧：解包新版本为 canary 并健康检查 |
| `remote-cutover-or-rollback.sh` | 服务器侧：canary 提升为 stable / 回滚到上一版本 |
| `deploy-gateway.ps1` | 本机编排：构建 → 上传 → canary → 灰度 → 切换 → 校验 |
| `../../docs/M6-独立ECS部署规划.md` | 拓扑、规格、成本、隔离边界 |

## 首次上线（M6）

1. 按 `docs/M6-独立ECS部署规划.md` 在控制台创建 ECS（2C4G，Alinux3，40G ESSD，独立安全组，密钥登录）。
2. 本机执行：
   ```powershell
   scp -i adguard.pem scripts... # 或直接 git clone 仓库到 ECS
   ssh -i adguard.pem root@<ECS_IP> "bash /opt/adguard-src/deploy/ecs/setup-ecs.sh"
   ```
3. 安装 systemd 单元：stable/canary/control-plane/pipeline 四个 unit + `daemon-reload` + `enable`。
4. 写 `/opt/adguard/config/{gateway,control-plane,pipeline}.env`（模板见 `config/`，密钥只存在服务器）。
5. 首次部署网关：`powershell -File deploy/ecs/deploy-gateway.ps1 -EcsHost <IP> -SshKey <pem>`
6. 部署控制面/流水线：打 jar / pipeline 源码包，`scp` 到 release 目录，重启对应 unit（第一次无 canary 需求）。
7. 冒烟：`scripts/smoke-m1.ps1 -Gateway http://<IP>:8080` 与 M3 冒烟（judge 需 pipeline/控制面在线）。
8. 写《P2 生产部署记录》+ 截图（健康检查、灰度过程、回滚演练）。

## 灰度与回滚演练（M6 验收）

```powershell
# 1) 发布新版本（自动 10% -> 50% -> 100%）
powershell -File deploy/ecs/deploy-gateway.ps1 -EcsHost <IP> -SshKey <pem> -Version v2
# 2) 回滚
powershell -File deploy/ecs/deploy-gateway.ps1 -EcsHost <IP> -SshKey <pem> -Rollback
```

演练记录要包括：每档灰度的 healthz 状态码、nginx 访问日志中 canary/stable 的分流证据、回滚后 stable 版本号与 healthz。

## 已知边界（诚实记录）

- **灰度只覆盖网关**（无状态、请求入口）；控制面/流水线升级用重启窗口（后续可做双实例）。
- 流水线在 2C4G 上的内存占用需实测（torch CPU + BGE 模型约 600–900MB），若超限降级为 `ADGUARD_EMBEDDER=hashing` 并在部署记录里写明指标影响。
- OSS 通过 MinIO 客户端访问（S3 兼容），真实验证需要 RAM 策略只允许 `adguard/` 前缀。
- nginx 单机是单点；ECS 级故障恢复依赖快照/重建，M6 只演练进程级故障与版本回滚。
