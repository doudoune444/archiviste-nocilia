"""Seeds a minimal corpus for CI offline eval runs.

Inserts documents and chunks into Postgres aligned with eval/fixtures/ci_smoke_qa.jsonl
so that context_recall_structural is meaningful in offline mode.

Each unique source_path referenced in expected_contexts gets one document + one chunk.
Chunk texts are lore-dummy narratives that contain the expected_answer_keywords embedded
in context — not a bare keyword join — so keyword_overlap_rate measures whether the
retrieve pipeline returns chunks that actually contain the relevant keywords.

Embeddings are hash-based pseudo-embeddings (SHA-256 of source_path, expanded to 1024
floats in [-1, 1], L2-normalised). They are deterministic, non-zero, and differentiated
per source_path. Real bge-m3 embeddings are not used in CI to avoid 500 MB model download.

NOTE on keyword_overlap_rate in offline mode: because real semantic search (bge-m3) is
not available in CI, the top-k retrieval order is driven by pgvector cosine distance
against the hash-based pseudo-embeddings, not semantic similarity. The metric therefore
measures "does the retrieve pipeline return the seeded chunks for this query?" — a
plumbing/integration check — not semantic relevance quality. Semantic quality is
validated in live mode via Ragas metrics. See eval/README.md for full rationale.

Requires DATABASE_URL env var (postgresql+asyncpg:// or postgresql://).
Skipped gracefully when DATABASE_URL is absent (local runs without DB).
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
from pathlib import Path

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ci_smoke_qa.jsonl"
EMBEDDING_DIM = 1024

# Lore-dummy narratives: keywords are present but embedded in narrative context,
# not a bare join. Each template uses {keywords} substitution for the canonical terms.
_LORE_TEMPLATES = [
    (
        "Au commencement du Quatrième Âge, {kw0} franchit le seuil de la bibliothèque ancestrale. "
        "{kw1} leva les yeux de son grimoire de cuir noir et reconnut l'envoyée du conseil."
    ),
    (
        "Les annales du {kw0} relatent que la cité de {kw1} fut fondée au crépuscule "
        "de la première ère, sur les ruines d'un sanctuaire oublié."
    ),
    (
        "La grande salle du {kw0} abritait des milliers de rouleaux. "
        "On y conservait la mémoire de {kw1} depuis des générations immémoriales."
    ),
    (
        "Le gardien du {kw0} connaissait chaque recoin de la demeure. "
        "{kw1} était gravé dans la pierre au-dessus de l'entrée principale."
    ),
]

# Fallback narrative for chunks without keywords (e.g. lore_gap, mystery modes).
_LORE_FALLBACK = (
    "Ce fragment d'archives provient du fonds {path}. "
    "Son contenu demeure partiellement illisible, rongé par les siècles."
)


def _lore_text(source_path: str, keywords: list[str]) -> str:
    """Generate a lore-dummy narrative text.

    When keywords are available they are embedded in the narrative so that
    keyword_overlap_rate measures genuine retrieval signal.  When no keywords
    are present (lore_gap / mystery entries) a fallback narrative is used so
    the chunk text is always longer than the bare source_path.
    """
    if not keywords:
        return _LORE_FALLBACK.format(path=source_path)
    template = _LORE_TEMPLATES[hash(source_path) % len(_LORE_TEMPLATES)]
    kw0 = keywords[0]
    kw1 = keywords[1] if len(keywords) > 1 else source_path
    return template.format(kw0=kw0, kw1=kw1)


def _hash_embedding(source_path: str) -> str:
    """Return a 1024-dim L2-normalised float vector derived from SHA-256 of source_path.

    Deterministic, non-zero, unique per source_path. Not semantically meaningful —
    used as a CI stand-in for real bge-m3 embeddings.
    """
    raw = hashlib.sha256(source_path.encode()).digest()
    # Expand 32 bytes → 1024 floats by cycling through the hash bytes as signed int8.
    floats: list[float] = []
    for i in range(EMBEDDING_DIM):
        byte_val = raw[i % len(raw)]
        # Map [0, 255] → [-1.0, 1.0]
        floats.append((byte_val - 127.5) / 127.5)

    # L2-normalise so cosine distance is well-defined.
    norm = sum(v * v for v in floats) ** 0.5
    if norm > 0:
        floats = [v / norm for v in floats]

    # Pack as binary for the struct-based format check, but we use text literal for pgvector.
    _ = struct.pack(f"{EMBEDDING_DIM}f", *floats)  # validate no overflow
    return "[" + ",".join(f"{v:.6f}" for v in floats) + "]"


def _collect_seed_data(fixture_path: Path) -> dict[str, tuple[str, str]]:
    """Return {source_path: (chunk_text, embedding_literal)} from CI smoke fixture."""
    seed: dict[str, tuple[str, str]] = {}
    with fixture_path.open(encoding="utf-8") as fh:
        for line in fh:
            entry = json.loads(line)
            keywords: list[str] = entry.get("expected_answer_keywords", [])
            for ctx in entry.get("expected_contexts", []):
                if ctx not in seed:
                    text = _lore_text(ctx, keywords)
                    embedding = _hash_embedding(ctx)
                    seed[ctx] = (text, embedding)
    return seed


def _insert_seed(seed: dict[str, tuple[str, str]], database_url: str) -> None:
    """Insert documents and chunks via psycopg2 (sync, schema already applied)."""
    try:
        import psycopg2  # noqa: PLC0415
    except ImportError:
        print(
            "seed_test_corpus: psycopg2 not available — skipping seed (install psycopg2-binary)",
            file=sys.stderr,
        )
        return

    # Normalize asyncpg URL to psycopg2-compatible
    url = database_url.replace("postgresql+asyncpg://", "postgresql://")

    conn = psycopg2.connect(url)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            for source_path, (chunk_text, embedding) in seed.items():
                content_hash = hashlib.sha256(source_path.encode()).hexdigest()
                cur.execute(
                    """
                    INSERT INTO documents (source_path, title, content_hash)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (source_path) DO UPDATE SET title = EXCLUDED.title
                    RETURNING id
                    """,
                    (source_path, source_path, content_hash),
                )
                row = cur.fetchone()
                doc_id = row[0] if row else None
                if doc_id is None:
                    cur.execute(
                        "SELECT id FROM documents WHERE source_path = %s", (source_path,)
                    )
                    doc_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO chunks (document_id, ord, text, embedding)
                    VALUES (%s, 0, %s, %s::vector)
                    ON CONFLICT (document_id, ord) DO UPDATE SET text = EXCLUDED.text
                    """,
                    (doc_id, chunk_text, embedding),
                )
        conn.commit()
        print(f"seed_test_corpus: inserted {len(seed)} documents+chunks")
    except Exception as exc:
        conn.rollback()
        print(f"seed_test_corpus: DB error — {exc}", file=sys.stderr)
        raise
    finally:
        conn.close()


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("seed_test_corpus: DATABASE_URL not set — skipping seed")
        return 0

    seed = _collect_seed_data(FIXTURE_PATH)
    if not seed:
        print("seed_test_corpus: no expected_contexts found in fixture — nothing to seed")
        return 0

    _insert_seed(seed, database_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
