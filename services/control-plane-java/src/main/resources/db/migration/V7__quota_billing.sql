-- M9 (FR-13): quota weights, price list and per-judgment usage ledger.
CREATE TABLE IF NOT EXISTS billing_price (
  code             VARCHAR(48)  NOT NULL PRIMARY KEY,
  service          VARCHAR(32)  NOT NULL,                 -- judge|parse|consistency
  asset_type       VARCHAR(16)  NULL,
  unit_price_micro BIGINT       NOT NULL,                 -- micro-CNY (1 CNY = 1,000,000)
  quota_weight     INT          NOT NULL DEFAULT 1,       -- daily quota points per event
  currency         CHAR(3)      NOT NULL DEFAULT 'CNY',
  updated_at       DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO billing_price (code, service, asset_type, unit_price_micro, quota_weight) VALUES
  ('judge.text',         'judge', 'text',         100,  1),
  ('judge.image',        'judge', 'image',        500,  2),
  ('judge.video',        'judge', 'video',       3000,  5),
  ('judge.landing_page', 'judge', 'landing_page', 1000, 3),
  ('consistency.image',  'consistency', 'image', 1500,  0),
  ('consistency.video',  'consistency', 'video', 2500,  0)
ON DUPLICATE KEY UPDATE
  service = VALUES(service), asset_type = VALUES(asset_type),
  unit_price_micro = VALUES(unit_price_micro), quota_weight = VALUES(quota_weight);

CREATE TABLE IF NOT EXISTS usage_record (
  id            CHAR(36)    NOT NULL PRIMARY KEY,
  tenant_id     VARCHAR(64) NOT NULL,
  asset_id      CHAR(36)    NOT NULL,
  judgment_id   CHAR(36)    NULL,
  asset_type    VARCHAR(16) NOT NULL,
  weight        INT         NOT NULL DEFAULT 1,
  cost_micro    BIGINT      NOT NULL DEFAULT 0,
  billed_date   DATE        NOT NULL,
  created_at    DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_usage_tenant_date (tenant_id, billed_date),
  KEY idx_usage_judgment (judgment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
-- Seed tenants used by existing demo data / acceptance smoke scripts.
INSERT IGNORE INTO tenant (id, name, status, daily_quota) VALUES
  ('demo-platform', 'Demo Platform', 'active', 100000),
  ('other-tenant', 'Other Tenant', 'active', 10000);
