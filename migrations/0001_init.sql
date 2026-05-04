-- Initial schema. Tables added in later migrations as features land.
-- The schema_version row for this file is inserted by migrations/run.sh,
-- not here, so the runner stays the single source of truth (FOUND-002).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS schema_version (
    version       INT PRIMARY KEY,
    applied_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description   TEXT NOT NULL
);
