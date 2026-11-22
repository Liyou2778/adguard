-- M2: regulation documents + clause-level structure (source of truth for rule payloads)
-- A "clause" is article-level (level='article') or item-level (level='item'); rules point at
-- the finest applicable clause so that M3 retrieval can return clause-grade evidence.

CREATE TABLE IF NOT EXISTS regulation (
  id             VARCHAR(64)  NOT NULL PRIMARY KEY,
  title          VARCHAR(256) NOT NULL,
  source_url     VARCHAR(512) NULL,
  effective_from DATE         NULL,
  checksum       CHAR(64)     NOT NULL,
  char_count     INT          NULL,
  created_at     DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at     DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS regulation_clause (
  clause_id        VARCHAR(64)  NOT NULL PRIMARY KEY,
  regulation_id    VARCHAR(64)  NOT NULL,
  article          VARCHAR(32)  NOT NULL,
  article_no       INT          NOT NULL,
  chapter          VARCHAR(64)  NULL,
  level            VARCHAR(16)  NOT NULL DEFAULT 'article',   -- article|item
  parent_clause_id VARCHAR(64)  NULL,
  item_label       VARCHAR(16)  NULL,                          -- （三）
  text             MEDIUMTEXT   NOT NULL,
  char_start       INT          NULL,
  char_end         INT          NULL,
  checksum         CHAR(64)     NOT NULL,
  created_at       DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_clause_regulation (regulation_id, article_no),
  KEY idx_clause_parent (parent_clause_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE rule
  ADD COLUMN clause_id VARCHAR(64) NULL,
  ADD COLUMN source_regulation_id VARCHAR(64) NULL,
  ADD KEY idx_rule_clause (clause_id);
