-- M7: asset parsing metadata (FR-1 landing_page ingestion + FR-2 multimodal parsing).
-- object_key stays the storage handle; landing_page assets have no object and carry source_url.
ALTER TABLE asset
  ADD COLUMN source_url VARCHAR(2048) NULL AFTER object_key,
  ADD COLUMN parse_status VARCHAR(16) NULL AFTER source_url,
  ADD COLUMN parse_meta JSON NULL AFTER parse_status;
