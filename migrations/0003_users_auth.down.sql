-- SEC-001 PR-a: Rollback auth schema additions.
--
-- Sessions table must be dropped first (FK references users).
-- Sentinel row (00000000-... tier='anonymous') is preserved throughout.

DROP TABLE IF EXISTS sessions;

DROP INDEX IF EXISTS users_email_lower_uq;

ALTER TABLE users
    DROP CONSTRAINT IF EXISTS users_auth_consistency;

ALTER TABLE users
    DROP COLUMN IF EXISTS password_hash,
    DROP COLUMN IF EXISTS email;
