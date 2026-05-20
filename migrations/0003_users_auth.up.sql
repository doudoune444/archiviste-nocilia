-- SEC-001 PR-a: Add auth columns to users + create sessions table.
--
-- users.email / users.password_hash are nullable so the existing sentinel
-- row (00000000-... tier='anonymous') created by migration 0002 is preserved
-- without change. The partial CHECK enforces that any non-anonymous row must
-- supply both email and password_hash.

ALTER TABLE users
    ADD COLUMN email         TEXT,
    ADD COLUMN password_hash TEXT;

ALTER TABLE users
    ADD CONSTRAINT users_auth_consistency
        CHECK (tier = 'anonymous' OR (email IS NOT NULL AND password_hash IS NOT NULL));

-- Case-insensitive unique index on email; NULLs (anonymous sentinel) excluded.
CREATE UNIQUE INDEX users_email_lower_uq
    ON users (LOWER(email))
    WHERE email IS NOT NULL;

-- Server-side sessions (AC-4, AC-8, AC-13).
-- token_hash stores argon2id(raw_token); never the raw token itself.
CREATE TABLE sessions (
    id          UUID PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ
);

-- FK index for cascade delete + per-user session listing.
CREATE INDEX sessions_user_id_idx ON sessions(user_id);
