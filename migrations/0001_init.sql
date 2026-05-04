-- Initial schema. Tables added in later migrations as features land.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS schema_version (
    version       INT PRIMARY KEY,
    applied_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description   TEXT NOT NULL
);

INSERT INTO schema_version (version, description)
VALUES (1, 'init: extensions vector + pgcrypto')
ON CONFLICT (version) DO NOTHING;
