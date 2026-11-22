# ADR-0002：分片上传、内容去重与幂等完成

- 状态：已接受（M1）
- 日期：2026-11-22
- 关联：PRD FR-1；P2-技术设计 §2、§8

## 背景

素材单文件可达 100 MB（M1 上限可配），且商家侧网络不稳定。接入层必须支持：

- 分片上传 + 断点续传（重试不能产生重复数据）；
- 重复上传按 digest 去重（同租户同内容只存一份）；
- 上传完成后触发异步审核，但**上传成功与否不能被下游阻塞**。

## 决策

### 1. 控制面与数据面分离：网关只协调，对象存 MinIO/OSS

```
POST /api/v1/assets                     建 asset + upload_session（状态 uploading）
PUT  /api/v1/assets/{id}/chunks/{index} 网关接收分片 -> 对象存储 tmp/{tenant}/{asset}/{index}
POST /api/v1/assets/{id}/complete       对象存储 ComposeObject 合并 -> 计算 sha256 -> 去重 -> 入队
```

- 合并使用对象存储服务端 `ComposeObject`，不落网关本地磁盘：网关保持无状态，可水平扩容。
- 分片下限 5 MiB：S3/MinIO 多段合并要求除最后一段外 ≥5 MiB。单分片上传不受限制。
- 对象键只由 ID 组成（`assets/{tenant}/{asset}/original`），文件名仅作为元数据入库——**文件名永远不参与对象键**，杜绝路径穿越。

### 2. 分片回执表保证重试幂等

`upload_chunk(asset_id, chunk_index)` 主键 + `INSERT IGNORE`。客户端重传同一分片不会增加 `received` 计数，`complete` 的完整性判断因此可靠。

### 3. 内容去重按租户隔离

- `asset (tenant_id, digest)` 唯一索引；digest = sha256(合并后完整对象)。
- 去重命中：删除本次合并对象、本 asset 置 `duplicate` 并记录 `deduped_from`、返回**已存在**的 asset_id 与 `deduped=true`。
- 绝不做跨租户去重：否则可被用来探测"别的租户是否上传过同样内容"（信息侧信道）。

### 4. 幂等完成 + 双重竞态处理

- `complete` 对已完成会话直接返回当前状态（200），不报错。
- 两个并发请求同时上传相同内容时，唯一索引兜底：第二个请求捕获 1062 后按"去重命中"处理。

### 5. 审核异步化，失败可恢复

`complete` 成功后向 Redis Stream `audit:tasks` 投递任务；投递失败**不回滚上传**，返回 202 + warning，由 M3 的 sweeper 补投（M1 仅记录日志）。

## 后果

**正面**

- 网关无状态，天然可多副本；对象存储直连避免网关转发大文件。
- 重试安全：分片、完成、去重三个环节都幂等。
- 租户隔离在存储与数据库两层都成立。

**负面 / 已知限制**

- 分片必须 ≥5 MiB（多分片场景）；小于 5 MiB 的文件只能单分片上传——文档已声明，客户端 SDK 需遵守。
- `complete` 需要回读整个对象计算 sha256，100 MB 对象约多做一次读放大；M5 若成为瓶颈可改为"客户端声明 digest + 服务端走 ETag 校验"。
- 临时分片对象删除是尽力而为（失败只记日志），依赖 M3 的清理任务兜底。
- 网关单实例限流（内存固定窗口），多副本部署前需换 Redis 限流（M6 事项）。

## 验证

- Go `internal/httpapi` 测试：完整闭环、去重返回首个 asset、跨租户 404、缺分片 409、错误分片大小 400、worker 重投幂等。
- M1 冒烟脚本 `scripts/smoke-m1.ps1`：真实 MySQL/Redis/MinIO 上重跑同一断言。
