-- M8 (FR-10): appeal review fields + single-open-appeal constraint + feedback pair provenance.
ALTER TABLE appeal
  ADD COLUMN asset_id CHAR(36) NULL AFTER judgment_id,
  ADD COLUMN original_decision VARCHAR(16) NULL AFTER final_decision,
  ADD COLUMN reviewed_by_user_id CHAR(36) NULL AFTER reviewer,
  ADD COLUMN reviewer_comment TEXT NULL AFTER reviewed_by_user_id,
  ADD COLUMN reviewed_at DATETIME(3) NULL AFTER reviewer_comment;

-- A judgment may have at most one open appeal (MySQL unique index ignores NULL values).
ALTER TABLE appeal
  ADD COLUMN open_judgment CHAR(36)
    GENERATED ALWAYS AS (CASE WHEN status = 'open' THEN judgment_id ELSE NULL END) STORED,
  ADD UNIQUE KEY uk_appeal_open_judgment (open_judgment);

ALTER TABLE feedback_pair
  ADD COLUMN appeal_id CHAR(36) NULL AFTER judgment_id,
  ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'appeal' AFTER tenant_id,
  ADD COLUMN review_comment TEXT NULL AFTER family,
  ADD COLUMN used_in_dpo_at DATETIME(3) NULL AFTER used_in_dpo,
  ADD KEY idx_feedback_appeal (appeal_id);
