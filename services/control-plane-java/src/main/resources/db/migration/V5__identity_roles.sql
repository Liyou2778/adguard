-- M8 (FR-10): identity, roles and audit log.
-- Platform staff (reviewer/admin) have tenant_id NULL; merchant users belong to one tenant.
CREATE TABLE IF NOT EXISTS app_user (
  id             CHAR(36)     NOT NULL PRIMARY KEY,
  tenant_id      VARCHAR(64)  NULL,
  username       VARCHAR(64)  NOT NULL,
  email          VARCHAR(190) NULL,
  password_hash  VARCHAR(100) NOT NULL,
  role           VARCHAR(16)  NOT NULL DEFAULT 'member',   -- owner|member|reviewer|admin
  status         VARCHAR(16)  NOT NULL DEFAULT 'active',   -- active|disabled
  last_login_at  DATETIME(3)  NULL,
  created_at     DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at     DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_app_user_username (username),
  KEY idx_app_user_tenant (tenant_id),
  KEY idx_app_user_role (role)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS audit_log (
  id              CHAR(36)    NOT NULL PRIMARY KEY,
  tenant_id       VARCHAR(64) NULL,
  actor_user_id   CHAR(36)    NULL,
  actor_username  VARCHAR(64) NULL,
  action          VARCHAR(64) NOT NULL,
  target_type     VARCHAR(32) NULL,
  target_id       VARCHAR(64) NULL,
  detail          JSON        NULL,
  created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_audit_tenant_time (tenant_id, created_at),
  KEY idx_audit_action (action)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
