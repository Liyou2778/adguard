-- AdGuard (P2) core schema -- M1
-- Owner: control-plane (Java / Flyway). Gateway (Go) and model services read/write data, never DDL.
-- Design notes:
--   * Every business table carries tenant_id (isolation at row level; FR-13).
--   * rule_version.payload is the immutable, complete rule definition (ADR-1: rule is data, not code).
--   * rule_snapshot.payload is an immutable set of active rule versions bound by judgments (ADR-3).
--   * asset dedupe is per tenant: UNIQUE (tenant_id, digest); MySQL allows multiple NULL digests
--     while uploads are still in progress.

CREATE TABLE IF NOT EXISTS tenant (
  id            VARCHAR(64)  NOT NULL PRIMARY KEY,
  name          VARCHAR(128) NOT NULL,
  status        VARCHAR(16)  NOT NULL DEFAULT 'active',
  daily_quota   INT          NOT NULL DEFAULT 100000,
  created_at    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS asset (
  id            CHAR(36)     NOT NULL PRIMARY KEY,
  tenant_id     VARCHAR(64)  NOT NULL,
  type          VARCHAR(16)  NOT NULL,                       -- image|video|text|landing_page
  status        VARCHAR(16)  NOT NULL,                       -- uploading|queued|judging|judged|failed|duplicate
  object_key    VARCHAR(512) NULL,
  digest        CHAR(64)     NULL,                           -- sha256 of the merged object
  size_bytes    BIGINT       NOT NULL,
  mime_type     VARCHAR(128) NULL,
  filename      VARCHAR(255) NULL,
  deduped_from  CHAR(36)     NULL,
  created_at    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_asset_tenant_digest (tenant_id, digest),
  KEY idx_asset_tenant_created (tenant_id, created_at),
  KEY idx_asset_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS upload_session (
  id               CHAR(36)   NOT NULL PRIMARY KEY,
  asset_id         CHAR(36)   NOT NULL,
  tenant_id        VARCHAR(64) NOT NULL,
  chunk_size       INT        NOT NULL,
  total_chunks     INT        NOT NULL,
  received_chunks  INT        NOT NULL DEFAULT 0,
  status           VARCHAR(16) NOT NULL DEFAULT 'uploading',  -- uploading|completed|aborted
  created_at       DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at       DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_upload_asset (asset_id),
  KEY idx_upload_tenant (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Chunk receipts make resumable uploads idempotent: re-uploading the same chunk index
-- must not increase the received counter (clients retry on flaky networks).
CREATE TABLE IF NOT EXISTS upload_chunk (
  asset_id    CHAR(36)    NOT NULL,
  tenant_id   VARCHAR(64) NOT NULL,
  chunk_index INT         NOT NULL,
  size_bytes  INT         NOT NULL,
  created_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (asset_id, chunk_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS rule (
  rule_id         VARCHAR(64)  NOT NULL PRIMARY KEY,
  tenant_id       VARCHAR(64)  NOT NULL,
  law             VARCHAR(128) NOT NULL,
  article         VARCHAR(32)  NOT NULL,
  item            VARCHAR(16)  NULL,
  family          VARCHAR(64)  NOT NULL,
  severity        VARCHAR(8)   NOT NULL,                     -- high|mid|low
  penalty_min     INT          NULL,
  penalty_max     INT          NULL,
  effective_from  DATE         NULL,
  effective_to    DATE         NULL,
  source_url      VARCHAR(512) NULL,
  current_version INT          NOT NULL DEFAULT 0,
  active_version  INT          NULL,
  status          VARCHAR(16)  NOT NULL DEFAULT 'draft',     -- draft|active|retired
  created_at      DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at      DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  KEY idx_rule_tenant_status (tenant_id, status),
  KEY idx_rule_family (family)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS rule_version (
  id          CHAR(36)    NOT NULL PRIMARY KEY,
  rule_id     VARCHAR(64) NOT NULL,
  tenant_id   VARCHAR(64) NOT NULL,
  version     INT         NOT NULL,
  payload     JSON        NOT NULL,
  status      VARCHAR(16) NOT NULL DEFAULT 'draft',          -- draft|active|retired
  created_by  VARCHAR(64) NULL,
  created_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_rule_version (rule_id, version),
  KEY idx_rule_version_tenant (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS rule_snapshot (
  id          CHAR(36)    NOT NULL PRIMARY KEY,
  tenant_id   VARCHAR(64) NOT NULL,
  seq         INT         NOT NULL,
  payload     JSON        NOT NULL,                          -- array of active rule definitions
  checksum    CHAR(64)    NOT NULL,                          -- sha256 of canonical payload
  rule_count  INT         NOT NULL,
  created_by  VARCHAR(64) NULL,
  created_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_snapshot_seq (tenant_id, seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS judgment (
  id                CHAR(36)    NOT NULL PRIMARY KEY,
  asset_id          CHAR(36)    NOT NULL,
  tenant_id         VARCHAR(64) NOT NULL,
  rule_snapshot_id  CHAR(36)    NULL,
  model_version     VARCHAR(64) NULL,
  decision          VARCHAR(16) NOT NULL,                    -- pass|block|manual
  reason            VARCHAR(512) NULL,
  latency_ms        INT         NULL,
  cost_micro        BIGINT      NULL,
  created_at        DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_judgment_asset (asset_id),
  KEY idx_judgment_tenant_created (tenant_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS violation (
  id            CHAR(36)     NOT NULL PRIMARY KEY,
  judgment_id   CHAR(36)     NOT NULL,
  rule_id       VARCHAR(64)  NOT NULL,
  span_start    INT          NULL,
  span_end      INT          NULL,
  bbox          JSON         NULL,
  evidence_text TEXT         NULL,
  confidence    DECIMAL(4,3) NULL,
  created_at    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_violation_judgment (judgment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS appeal (
  id              CHAR(36)    NOT NULL PRIMARY KEY,
  judgment_id     CHAR(36)    NOT NULL,
  tenant_id       VARCHAR(64) NOT NULL,
  reason          VARCHAR(64) NULL,
  detail          TEXT        NULL,
  status          VARCHAR(16) NOT NULL DEFAULT 'open',       -- open|accepted|rejected
  reviewer        VARCHAR(64) NULL,
  final_decision  VARCHAR(16) NULL,
  created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  KEY idx_appeal_judgment (judgment_id),
  KEY idx_appeal_tenant (tenant_id),
  KEY idx_appeal_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS feedback_pair (
  id          CHAR(36)    NOT NULL PRIMARY KEY,
  judgment_id CHAR(36)    NOT NULL,
  tenant_id   VARCHAR(64) NOT NULL,
  chosen      JSON        NOT NULL,
  rejected    JSON        NOT NULL,
  family      VARCHAR(64) NULL,
  used_in_dpo TINYINT     NOT NULL DEFAULT 0,
  created_at  DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_feedback_tenant (tenant_id),
  KEY idx_feedback_dpo (used_in_dpo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
