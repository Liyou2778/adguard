# M1 接口契约（v0.1）

> 服务：Go 接入网关（:8080）+ Java 控制面（:8081）
> 约定：JSON UTF-8；字段 snake_case；所有错误统一信封；租户通过 `X-Tenant-Id` 传递。

## 0. 通用约定

### 0.1 请求头

| 头 | 必需 | 说明 |
|---|---|---|
| `X-Tenant-Id` | 是（`/api/v1/*`、`/admin/*`） | 租户 ID，`^[a-z0-9][a-z0-9_-]{2,63}$` |
| `X-Request-Id` | 否 | 不传则服务端生成；响应必定回带 |
| `X-API-Key` | 仅当网关配置了 `GATEWAY_API_KEY` | M1 可选鉴权 |
| `Content-Type` | 写接口 | `application/json` 或分片用 `application/octet-stream` |

### 0.2 错误信封

```json
{
  "error": {"code": "INCOMPLETE_UPLOAD", "message": "not all chunks were received",
             "details": {"received": 1, "total": 2}},
  "request_id": "6f1f..."
}
```

| HTTP | code 示例 | 含义 |
|---|---|---|
| 400 | `MISSING_TENANT` `INVALID_TENANT` `INVALID_BODY` `INVALID_ASSET_TYPE` `INVALID_CHUNK_SIZE` `INVALID_CHUNK_INDEX` `INVALID_RULE` `RULE_ID_MISMATCH` | 参数/载荷错误 |
| 401 | `UNAUTHORIZED` | API Key 缺失或错误 |
| 404 | `ASSET_NOT_FOUND` `JUDGMENT_NOT_FOUND` `RULE_NOT_FOUND` `SNAPSHOT_NOT_FOUND` | 资源不存在（跨租户同样返回 404） |
| 409 | `INCOMPLETE_UPLOAD` `UPLOAD_NOT_OPEN` `RULE_EXISTS` `RULE_ALREADY_ACTIVE` `NO_ACTIVE_RULES` `ASSET_NOT_READY` | 状态冲突 |
| 413 | `ASSET_TOO_LARGE` `CHUNK_TOO_LARGE` | 体积超限 |
| 429 | `RATE_LIMITED` | 租户限流（响应头 `Retry-After` 秒） |
| 500 | `INTERNAL_ERROR` | 服务端错误，详见日志 request_id |

## 1. 接入网关（Go，`http://127.0.0.1:8080`）

### 1.1 创建上传

`POST /api/v1/assets`

```json
{
  "filename": "双11主图.png",
  "size_bytes": 10485760,
  "mime_type": "image/png",
  "type": "image",
  "chunk_size": 5242880
}
```

| 字段 | 说明 |
|---|---|
| `type` | `image` / `video` / `text` / `landing_page` |
| `size_bytes` | >0，且 ≤ `GATEWAY_MAX_ASSET_BYTES`（默认 100 MiB） |
| `chunk_size` | 可选；缺省 5 MiB；范围 [1 MiB, 64 MiB]；需 ≥5 MiB 才能多分片（S3 限制） |

响应 `201`：

```json
{"asset_id":"...","upload_id":"...","chunk_size":5242880,"total_chunks":2,"max_asset_bytes":104857600}
```

### 1.2 上传分片

`PUT /api/v1/assets/{asset_id}/chunks/{index}`，body 为原始字节。

- `index` ∈ [0, total_chunks)；除最后一片外，每片必须恰好 `chunk_size` 字节。
- 幂等：同一 index 重传覆盖对象、不重复计数。
- 响应 `204`，头：`X-Chunks-Received`、`X-Chunks-Total`。

### 1.3 完成上传

`POST /api/v1/assets/{asset_id}/complete`（无 body）

- 分片不全 → `409 INCOMPLETE_UPLOAD`（details 含 received/total）。
- 首次成功 → `202`：`{"asset_id","status":"queued","digest","size_bytes"}`
- 内容重复 → `200`：`{"asset_id": <已存在资产>, "status":"duplicate", "deduped": true, "digest"}`
- 重复调用 → `200`：返回当前状态。

### 1.4 查询资产

`GET /api/v1/assets/{asset_id}` → `200`

```json
{
  "asset_id": "...", "tenant_id": "demo-platform", "type": "image",
  "status": "queued|judging|judged|duplicate|failed",
  "object_key": "assets/demo-platform/.../original", "digest": "...",
  "size_bytes": 10485760, "mime_type": "image/png", "filename": "双11主图.png",
  "created_at": "2026-11-22T21:30:00Z", "updated_at": "...",
  "latest_judgment_id": "..."
}
```

### 1.5 下载地址

`GET /api/v1/assets/{asset_id}/download-url` → `200`

```json
{"asset_id":"...","url":"http://127.0.0.1:9000/adguard-assets/assets/...?X-Amz-...","expires_in_seconds":900}
```

未完成上传 → `409 ASSET_NOT_READY`。签名 URL 使用**公网可达端点**（本地开发时与内网端点一致）。

### 1.6 查询判定

`GET /api/v1/judgments/{judgment_id}` → `200`

```json
{
  "judgment_id": "...", "asset_id": "...", "tenant_id": "demo-platform",
  "rule_snapshot_id": "<绑定快照，M1 为控制面最新快照；控制面不可用时为空>",
  "model_version": "stub-m1-v0",
  "decision": "manual",
  "reason": "M1 stub judge: ... routes to human review by design (ADR-2)",
  "latency_ms": 1, "cost_micro": 0,
  "violations": [],
  "created_at": "..."
}
```

M1 规则：**所有资产判 `manual`（转人工）**，原因是真实流水线尚未实现（M2–M4），且 ADR-2 要求宁可转人工不可误放。`violations` 恒为数组，永不返回 null。

### 1.7 运维端点

| 路径 | 说明 |
|---|---|
| `GET /healthz` | 存活探针，恒 200 |
| `GET /readyz` | 依赖就绪（MySQL schema + Redis），失败 503 |
| `GET /metrics` | Prometheus 指标（HTTP、资产创建、上传字节、判定数、失败数） |

## 2. 控制面（Java，`http://127.0.0.1:8081`）

### 2.1 建规则

`POST /admin/rules` + `X-Tenant-Id`

body 见 ADR-0001 的规则体。响应 `201`：

```json
{
  "summary": {"rule_id":"ADV-LAW-ART9-3","tenant_id":"demo-platform","law":"中华人民共和国广告法",
              "article":"第九条","item":"（三）","family":"absolute_terms","severity":"high",
              "status":"draft","current_version":1,"active_version":null,"updated_at":"..."},
  "versions": [{"version":1,"status":"draft","created_by":"api","created_at":"...",
                "payload": {"rule_id":"ADV-LAW-ART9-3", "...": "..."}}]
}
```

已存在 → `409 RULE_EXISTS`。

### 2.2 改规则（产生新草稿版本）

`PUT /admin/rules/{rule_id}`，实现条件：body.rule_id 必须等于路径。

### 2.3 规则列表 / 详情

`GET /admin/rules?status=&family=&limit=`、`GET /admin/rules/{rule_id}`。

### 2.4 激活并生成快照

`POST /admin/rules/{rule_id}/activate` → `200`

```json
{
  "id": "snapshot-uuid", "tenant_id": "demo-platform", "seq": 1, "rule_count": 1,
  "checksum": "sha256-hex", "created_at": "...",
  "payload": [{"rule_id": "ADV-LAW-ART9-3", "...": "..."}]
}
```

已是最新激活版本 → `409 RULE_ALREADY_ACTIVE`；租户无激活规则 → `409 NO_ACTIVE_RULES`（发生在首次激活前的极端情况）。

### 2.5 快照下发（内部）

`GET /internal/rules/snapshot?tenant_id=demo-platform` → 最新快照
`GET /internal/rules/snapshot?tenant_id=demo-platform&snapshot_id=...` → 指定快照（用于复现判定）

> M1 该接口仅在受信网络暴露；M6 增加服务令牌 / mTLS 与网络策略。
