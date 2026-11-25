-- M12: account security hardening (first-login password change, login lock / rate limiting).
-- TOTP columns are reserved for the optional 2FA rollout (not enforced yet).
ALTER TABLE app_user
  ADD COLUMN must_change_password TINYINT NOT NULL DEFAULT 0 AFTER status,
  ADD COLUMN failed_login_attempts INT NOT NULL DEFAULT 0 AFTER must_change_password,
  ADD COLUMN locked_until DATETIME(3) NULL AFTER failed_login_attempts,
  ADD COLUMN totp_secret VARCHAR(64) NULL AFTER locked_until,
  ADD COLUMN totp_enabled TINYINT NOT NULL DEFAULT 0 AFTER totp_secret;
