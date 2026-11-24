# M6 ECS 模拟演练（WSL 上完整还原 ECS 部署面）

> 目的：在真实 ECS 创建前，用 WSL（systemd 可用）+ 部署包 + 真实依赖，完整走一遍 **systemd 单元、nginx 灰度、cutover、回滚**，
> 把配置级/脚本级 bug 全部提前暴露。日期：2026-11-25。
> 环境：WSL2 Ubuntu 24.04（nginx 1.24、OpenJDK 21、Python 3.12）、Windows MySQL 走 WSL 原生 MySQL :3307、
> Redis/MinIO 为 Windows Docker（WSL 经宿主 IP 访问）、部署包 `adguard-bundle-m6sim.tar.gz`。

## 1. 拓扑还原

```
curl -> nginx :8080 (WSL, 与 setup-ecs.sh 同配置)
          -> stable  :18080 (systemd adguard-gateway-stable, /opt/adguard/current/gateway)
          -> canary  :28080 (systemd adguard-gateway-canary,  /opt/adguard/canary/gateway)
        control-plane :8081 (systemd, Java 21)
        pipeline      :8090 (systemd, venv + hashing embedder)
        MySQL 127.0.0.1:3307 / Redis / MinIO(宿主)
```

`/opt/adguard` 目录结构、systemd 单元、`config/*.env`、`releases/current/canary` 软链与 `deploy/ecs/` 脚本完全一致。

## 2. 结果

| 检查 | 结果 |
|---|---|
| systemd 服务 | control-plane / pipeline / gateway-stable / nginx 全部 **active**，healthz 200 |
| **M1 冒烟（经 nginx :8080）** | **29/29 PASS**（上传→存储→异步判定→查询→快照→去重→租户隔离→metrics） |
| 灰度 10%（300 请求） | stable 245 / canary 38 ≈ **12.7%**（hash 方差） |
| 灰度 50%（300 请求） | stable 146 / canary 154 ≈ **51.3%** |
| 灰度 100%（300 请求） | canary 296 / stable 4（切换瞬间 in-flight） |
| cutover | `STABLE_HEALTHY v6`，nginx 回 0% 后 **8080 连续 5 秒 200**（无 5xx） |
| 回滚 | `ROLLBACK_OK`，current 回到 v3，**8080 200** |
| 反向验证（旧行为） | 若 cutover 后立刻 `systemctl stop canary` → 出现 **1 次瞬时 502**（旧 nginx worker 仍在把 in-flight 请求发给 canary） |

## 3. 演练发现并修复的真实问题

| # | 问题 | 影响 | 修复 |
|---|---|---|---|
| 1 | `adguard-pipeline.service` 未设置 `PYTHONPATH` | 服务启动即 `ModuleNotFoundError: adguard_pipeline`，pipeline 永远起不来 | unit 增加 `Environment=PYTHONPATH=/opt/adguard/current/pipeline/src`、`PYTHONUNBUFFERED=1` |
| 2 | 部署包缺少 `pipeline/data`（parsers） | 即使服务起来，条款加载也会失败 | `package-bundle.ps1` 打包 `pipeline-data`；`remote-deploy-bundle.sh` 复制到 `current/pipeline/data` |
| 3 | cutover 后立即停 canary | 旧 nginx worker 仍把 in-flight 请求发给 canary → 瞬时 502 | cutover 后**保持 canary 运行**（0% 空闲），由下次发布回收；`deploy-gateway.ps1` 已改 |
| 4 | upstream `max_fails=3 fail_timeout=10s` | 单实例重启会被动标记 down 最长 10s，放大为故障窗口 | 改为 `max_fails=0`；并加 access_log（含 `$upstream_addr`）便于灰度取证 |
| 5 | 0% 灰度 `split_clients` 非法 | 首次部署 `nginx -t` 失败 | 0% 生成"无 split_clients、直连 stable"配置（此前已修复） |

## 4. 尚未验证（必须等真实 ECS）

- RDS MySQL / OSS + RAM 真实凭据与内网连通；
- 公网安全组、域名与 8080 暴露；
- 2C4G 内存上限下 control-plane/pipeline 的 `MemoryMax` 实际约束（WSL 内存更宽裕）；
- Alibaba Cloud Linux 3 与 Ubuntu 24.04 的包名差异（`setup-ecs.sh` 已双支持，但未在 Alinux3 实数）；
- 真实流量下 10%→50%→100% 的观测与回滚（本演练只有健康检查流量）。

## 5. 复现

```bash
# 1) 打包（含 pipeline data）
powershell -File deploy/ecs/package-bundle.ps1 -Version m6sim
# 2) WSL 内还原 ECS 目录/单元/nginx
bash .tmp/wsl_ecs_setup.sh
# 3) 灰度 + cutover + 回滚
bash .tmp/wsl_ecs_drill2.sh
bash .tmp/final_cutover.sh
```
