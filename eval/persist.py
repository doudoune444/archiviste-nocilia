"""Postgres persistence for Ragas eval runs.

Writes a single aggregated row per live run into the eval_runs table.
No LLM payload is stored — only numeric metrics and run metadata (AC-9).
INSERT is always parameterised; never f-string/format into SQL (AC-8).
DATABASE_URL is read from the environment with no fallback (AC-10, secret-hygiene).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

# psycopg2 is untyped; cast() and explicit local annotations keep mypy --strict happy
# without ever using # type: ignore (no-workaround rule).
import psycopg2
import psycopg2.extensions

_INSERT_SQL = (
    "INSERT INTO eval_runs"
    " (id, git_sha, runner_mode, golden_set_version,"
    "  faithfulness, answer_relevancy, context_precision, context_recall,"
    "  entries_total, entries_ok, entries_errors, started_at, finished_at)"
    " VALUES"
    " (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


class PersistError(Exception):
    """Raised when the eval_runs INSERT fails for any reason."""


@dataclass
class EvalRunRow:
    """Columns to insert into eval_runs (id and created_at are DB-generated / Python-side uuid).

    All metric fields are float because live-mode NOT NULL columns forbid None.
    The caller must validate that no metric is None before constructing this row.
    Zero LLM payload fields: no answer, question, contexts, or citations (AC-9).
    """

    git_sha: str
    runner_mode: str
    golden_set_version: str
    faithfulness: float
    answer_relevancy: float
    context_precision: float
    context_recall: float
    entries_total: int
    entries_ok: int
    entries_errors: int
    started_at: str
    finished_at: str


def derive_golden_set_version(path: Path) -> str:
    """Return the SHA-256 hex digest of the golden set file's raw bytes (AC-6).

    Deterministic: two calls on the same bytes produce the same string.
    A single-byte change produces a different digest.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def persist_eval_run(row: EvalRunRow) -> str:
    """Insert one row into eval_runs and return the inserted UUID string.

    Reads DATABASE_URL from the environment; raises KeyError if absent (no default,
    secret-hygiene §Production).  On any psycopg2 error, raises PersistError with a
    generic message — the raw exception (which may embed the DSN) is never forwarded
    to the caller or logged (AC-10, AC-11).
    """
    database_url: str = os.environ["DATABASE_URL"]
    run_id = str(uuid.uuid4())
    params = (
        run_id,
        row.git_sha,
        row.runner_mode,
        row.golden_set_version,
        row.faithfulness,
        row.answer_relevancy,
        row.context_precision,
        row.context_recall,
        row.entries_total,
        row.entries_ok,
        row.entries_errors,
        row.started_at,
        row.finished_at,
    )
    conn: Any = None
    cursor: Any = None
    try:
        conn = cast(psycopg2.extensions.connection, psycopg2.connect(database_url))
        cursor = cast(psycopg2.extensions.cursor, conn.cursor())
        cursor.execute(_INSERT_SQL, params)
        conn.commit()
    except Exception as exc:
        raise PersistError("eval_runs insert failed") from exc
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()
    return run_id
