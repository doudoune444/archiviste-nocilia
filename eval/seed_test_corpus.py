"""Seeds a minimal corpus for CI offline eval runs.

Inserts documents and chunks into Postgres aligned with eval/fixtures/ci_smoke_qa.jsonl
so that context_recall_structural is meaningful in offline mode.

Each unique source_path referenced in expected_contexts gets one document + one chunk
with a dummy zero-embedding. The chunk text contains the expected_answer_keywords so
keyword_overlap_rate can be non-trivially measured against retrieved content.

Requires DATABASE_URL env var (postgresql+asyncpg:// or postgresql://).
Skipped gracefully when DATABASE_URL is absent (local runs without DB).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ci_smoke_qa.jsonl"
ZERO_EMBEDDING = "[" + ",".join(["0"] * 1024) + "]"


def _collect_seed_data(fixture_path: Path) -> dict[str, str]:
    """Return {source_path: chunk_text} derived from CI smoke fixture."""
    seed: dict[str, str] = {}
    with fixture_path.open(encoding="utf-8") as fh:
        for line in fh:
            entry = json.loads(line)
            keywords: list[str] = entry.get("expected_answer_keywords", [])
            keyword_text = " ".join(keywords) if keywords else entry["id"]
            for ctx in entry.get("expected_contexts", []):
                if ctx not in seed:
                    seed[ctx] = keyword_text
    return seed


def _insert_seed(seed: dict[str, str], database_url: str) -> None:
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
            for source_path, chunk_text in seed.items():
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
                    (doc_id, chunk_text, ZERO_EMBEDDING),
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
