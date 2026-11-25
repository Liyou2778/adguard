-- M10 (FR-6): merchant caption used for cross-modal consistency checks (image/video assets).
ALTER TABLE asset
  ADD COLUMN caption VARCHAR(2048) NULL AFTER filename;
