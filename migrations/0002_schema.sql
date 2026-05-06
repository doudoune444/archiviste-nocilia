-- description: schema walking skeleton
-- FOUND-003: six fundational tables (users, documents, chunks, conversations,
-- tickets, query_log) + HNSW index on chunks.embedding + sentinel users row.
-- Schema_version row inserted by migrations/run.sh, not here.

CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tier        TEXT NOT NULL CHECK (tier IN ('anonymous', 'member', 'author')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Sentinel anonymous user. ON CONFLICT keeps re-application safe (defence in
-- depth on top of schema_version skip in run.sh).
INSERT INTO users (id, tier)
VALUES ('00000000-0000-0000-0000-000000000000', 'anonymous')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE documents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_path   TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    tags          TEXT[] NOT NULL DEFAULT '{}',
    access_tier   TEXT NOT NULL DEFAULT 'public'
                  CHECK (access_tier IN ('public', 'members', 'author_only')),
    content_hash  TEXT NOT NULL,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE chunks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id  UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord          INT NOT NULL,
    text         TEXT NOT NULL,
    embedding    vector(1024) NOT NULL,
    UNIQUE (document_id, ord)
);

-- HNSW + cosine: matches retrieval similarity used by bge-m3 (1024-dim).
-- m / ef_construction left at pgvector defaults; revisit when corpus > 10k chunks.
CREATE INDEX chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE conversations (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES users(id),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    gcs_uri        TEXT NOT NULL UNIQUE,
    message_count  INT NOT NULL DEFAULT 0 CHECK (message_count >= 0)
);

CREATE TABLE tickets (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id  UUID NOT NULL REFERENCES conversations(id) ON DELETE RESTRICT,
    question         TEXT NOT NULL,
    category         TEXT NOT NULL DEFAULT 'uncategorized',
    priority_score   INT NOT NULL DEFAULT 1 CHECK (priority_score >= 1),
    status           TEXT NOT NULL DEFAULT 'open'
                     CHECK (status IN ('open', 'resolved', 'dismissed')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE query_log (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id         UUID NOT NULL UNIQUE,
    user_id            UUID NOT NULL REFERENCES users(id),
    conversation_id    UUID NULL REFERENCES conversations(id) ON DELETE SET NULL,
    query_text         TEXT NOT NULL,
    intent             TEXT NULL,
    mode               TEXT NULL
                       CHECK (mode IS NULL OR mode IN ('canon', 'off_topic', 'lore_gap', 'mystery')),
    status_code        INT NOT NULL,
    latency_ms         INT NOT NULL CHECK (latency_ms >= 0),
    prompt_tokens      INT NULL,
    completion_tokens  INT NULL,
    cost_eur           NUMERIC(10, 6) NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX query_log_user_created_idx
    ON query_log (user_id, created_at DESC);

CREATE INDEX query_log_created_idx
    ON query_log (created_at);
