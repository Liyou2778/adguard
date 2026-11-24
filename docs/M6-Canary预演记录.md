# M6 Canary 预演记录（本地 Docker，无需 ECS）

> 目的：在 ECS 创建前，先验证 `deploy/ecs/` 的 nginx 灰度配置、`switch-canary.sh` 生成逻辑与
> 分流语义，避免把配置级 bug 带到生产服务器上。
> 日期：2026-11-25 ｜ 环境：Windows + Docker Desktop（nginx:1.27-alpine、busybox httpd）

## 1. 方法

- 用 docker network 起两个上游：`adg-r2-stable` / `adg-r2-canary`（busybox httpd，分别返回 STABLE / CANARY）；
- nginx 容器挂载与 `deploy/ecs/switch-canary.sh` **同结构**的配置（上游名 + split_clients + proxy_pass）；
- 用不同 `X-Tenant-Id` 发起请求，统计命中 stable/canary 的数量；
- 依次切换 10% → 50% → 100% → 0%，每次先 `nginx -t` 再 reload。

## 2. 实测结果

| 灰度比例 | stable | canary | 失败 | 请求数 | 实测 canary 占比 | nginx -t |
|---:|---:|---:|---:|---:|---:|---|
| 10% | 173 | 27 | 0 | 200 | **13.5%** | 通过 |
| 50% | 50 | 50 | 0 | 100 | **50.0%** | 通过 |
| 100% | 0 | 100 | 0 | 100 | **100%** | 通过 |
| 0% | 100 | 0 | 0 | 100 | **0%** | 通过 |

> 10% 档实测 13.5% 属 murmur hash 分桶方差（200 个租户、期望 20，标准差约 4.2）；50%/100%/0% 与配置完全一致。

## 3. 发现并修复的 bug（重要）

**nginx `split_clients` 不接受 0% 或 0.0%**：`nginx -t` 报
`invalid percent value "0%"` / `"0.0%"`。

- 影响：原 `setup-ecs.sh` 默认配置（0% 灰度）会让 `nginx -t` 失败，首次部署直接卡住；
- 修复：`switch-canary.sh` 在 0% 时**不生成 split_clients 块**，直接 `proxy_pass http://adguard_stable`；
  `setup-ecs.sh` 的初始配置同样改为无 split_clients 的 stable-only 配置。
- 验证：0/10/50/100 四份生成配置均通过 `nginx -t`（见上表）。

## 4. 分流语义（写进 README，避免误读）

- split key = `${remote_addr}${http_x_tenant_id}` → **同一租户/来源 IP 粘性**落到同一版本；
- 因此 "10%" 指**约 10% 的租户/客户端**，不是 10% 的请求；
- 好处：同一租户不会在灰度期间来回切版本；坏处：按请求计的转化指标需要按租户聚合。

## 5. 其他已验证项

- `deploy/ecs/*.sh` 全部 `bash -n` 通过；
- 4 个 systemd unit 均含 `[Unit]/[Service]/ExecStart`，可被 systemd 解析；
- `setup-ecs.sh` 改为**同时支持 Alibaba Cloud Linux 3（dnf）与 Ubuntu 24.04（apt）**：
  包管理器自动探测；Java 包名分别用 `java-21-openjdk-headless` / `openjdk-21-jdk-headless`；
- 新增 `install-units.sh`：一条命令安装 4 个 unit、enable stable/control-plane/pipeline、canary 保持禁用。

## 6. 尚未验证（真实 ECS 上补）

- systemd 实际拉起进程、`EnvironmentFile` 权限、`MemoryMax` 生效情况；
- nginx 对真实网关端口的转发、`/healthz` 探活、日志切分；
- RDS MySQL / OSS / RAM 策略；
- 真实流量下的 10%→50%→100% 切换与回滚演练（含 canary 故障注入）。
