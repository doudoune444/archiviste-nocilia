-- description: GEN-004 — tickets.question_embedding vector(1024) + HNSW partial index + B-tree indexes
-- AC-20: add question_embedding column and indexes for cosine dedup and dashboard queries.
-- Nullable for back-compat with any pre-existing tickets (table is empty in practice at this stage).

ALTER TABLE tickets ADD COLUMN question_embedding vector(1024) NULL;

-- HNSW partial index: excludes NULLs (pgvector >= 0.5 required for WHERE clause on HNSW).
CREATE INDEX tickets_question_embedding_idx
    ON tickets USING hnsw (question_embedding vector_cosine_ops)
    WHERE question_embedding IS NOT NULL;

-- B-tree index: dashboard UI-002 lookup + FK join.
CREATE INDEX tickets_conversation_id_idx
    ON tickets (conversation_id);

-- B-tree index: dashboard sort by priority descending within open tickets.
CREATE INDEX tickets_status_priority_idx
    ON tickets (status, priority_score DESC, updated_at DESC);
