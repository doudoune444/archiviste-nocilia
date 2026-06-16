-- description: MEM-001 — conversation_messages (structured per-turn store, best-effort double-write)

CREATE TABLE conversation_messages (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id  UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    ordinal          INT NOT NULL CHECK (ordinal >= 0),
    content          TEXT NOT NULL,
    token_count      INT NOT NULL CHECK (token_count >= 0),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (conversation_id, ordinal)
);

-- Supports bounded tail read newest-first (MEM-001) and the token-budget window (MEM-002).
CREATE INDEX conversation_messages_tail_idx
    ON conversation_messages (conversation_id, ordinal DESC);
