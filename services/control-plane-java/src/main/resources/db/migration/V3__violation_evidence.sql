-- M3: clause-level violation evidence (FR-8). M1 only had rule_id; M3 judgments cite
-- the exact clause, its revision and the penalty range so the decision is auditable.

ALTER TABLE violation
  ADD COLUMN family VARCHAR(64) NULL,
  ADD COLUMN canonical VARCHAR(64) NULL,
  ADD COLUMN variant_kind VARCHAR(16) NULL,
  ADD COLUMN clause_id VARCHAR(64) NULL,
  ADD COLUMN regulation_id VARCHAR(64) NULL,
  ADD COLUMN article VARCHAR(32) NULL,
  ADD COLUMN item_label VARCHAR(16) NULL,
  ADD COLUMN clause_revision CHAR(64) NULL,
  ADD COLUMN clause_text MEDIUMTEXT NULL,
  ADD COLUMN penalty_law_ref VARCHAR(32) NULL,
  ADD COLUMN penalty_min INT NULL,
  ADD COLUMN penalty_max INT NULL,
  ADD KEY idx_violation_rule (rule_id),
  ADD KEY idx_violation_clause (clause_id);
