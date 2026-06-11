"""Postgres persistence for Ragas eval runs.

Writes a single aggregated row per live run into the eval_runs table.
No LLM payload is stored — only numeric metrics and run metadata (AC-9).
INSERT is always parameterised; never f-string/format into SQL (AC-8).
DATABASE_URL is read from the environment with no fallback (AC-10, secret-hygiene).

When CLOUD_SQL_IAM_AUTH=true (prod), an IAM OAuth access token is fetched via
google.auth and passed as the psycopg2 password — the DSN carries the IAM SA
username and Cloud SQL socket path but no password (prod DATABASE_URL has no
inline password).  The token is never logged (security.md §A09).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

# google.auth provides Application Default Credentials for the Cloud SQL IAM token.
# google-auth ships no type stubs; ignore_missing_imports covers it in pyproject.toml.
import google.auth
import google.auth.transport.requests

# psycopg2 is untyped; cast() and explicit local annotations keep mypy --strict happy
# without ever using # type: ignore (no-workaround rule).
import psycopg2
import psycopg2.extensions
from pydantic import SecretStr

_INSERT_SQL = (
    "INSERT INTO eval_runs"
    " (id, git_sha, runner_mode, golden_set_version,"
    "  faithfulness, answer_relevancy, context_precision, context_recall,"
    "  entries_total, entries_ok, entries_errors, started_at, finished_at)"
    " VALUES"
    " (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)

# Cloud SQL IAM auth scope — mirrors workers' auth_metadata/token.py CLOUD_SQL_SCOPE.
_CLOUD_SQL_IAM_SCOPE = "https://www.googleapis.com/auth/sqlservice.admin"


class PersistError(Exception):
    """Raised when the eval_runs INSERT fails for any reason.

    Carries safe diagnostic fields (error_class, pgcode) that callers may log
    without leaking DSN or token bytes (security.md §A09).
    """

    def __init__(self, message: str, *, error_class: str, pgcode: str | None) -> None:
        super().__init__(message)
        self.error_class: str = error_class
        self.pgcode: str | None = pgcode


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


def _fetch_iam_db_token() -> SecretStr:
    """Fetch a Cloud SQL IAM OAuth access token from Application Default Credentials.

    Used only when CLOUD_SQL_IAM_AUTH=true.  The token is the DB password for
    IAM-authenticated Cloud SQL connections; it is wrapped in SecretStr so it
    redacts in any repr/log output (security.md §A09 — mirrors the workers'
    auth_metadata/token.py).

    google.auth stubs are incomplete (ignore_missing_imports in pyproject.toml);
    explicit casts satisfy mypy --strict without # type: ignore.
    """
    # google.auth.default returns (Credentials, project); Credentials is untyped
    # in the stub-less google-auth package — cast to Any so attribute access type-checks.
    creds: Any
    creds, _ = google.auth.default(scopes=[_CLOUD_SQL_IAM_SCOPE])
    # cast the Request() call result to Any so mypy accepts creds.refresh(request)
    request: Any = google.auth.transport.requests.Request()
    creds.refresh(request)
    return SecretStr(cast(str, creds.token))


def persist_eval_run(row: EvalRunRow) -> str:
    """Insert one row into eval_runs and return the inserted UUID string.

    Reads DATABASE_URL from the environment; raises KeyError if absent (no default,
    secret-hygiene §Production).  When CLOUD_SQL_IAM_AUTH=true, fetches a Cloud SQL
    IAM access token and passes it as the psycopg2 password (overrides any inline
    password in the DSN).  On any error, raises PersistError carrying error_class and
    pgcode — never the DSN or token (AC-10, AC-11, security.md §A09).
    """
    database_url: str = os.environ["DATABASE_URL"]
    use_iam_auth = os.getenv("CLOUD_SQL_IAM_AUTH") == "true"
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
        if use_iam_auth:
            iam_token = _fetch_iam_db_token()
            conn = cast(
                psycopg2.extensions.connection,
                psycopg2.connect(database_url, password=iam_token.get_secret_value()),
            )
        else:
            conn = cast(psycopg2.extensions.connection, psycopg2.connect(database_url))
        cursor = cast(psycopg2.extensions.cursor, conn.cursor())
        cursor.execute(_INSERT_SQL, params)
        conn.commit()
    except Exception as exc:
        error_class = type(exc).__name__
        pgcode: str | None = getattr(exc, "pgcode", None)
        raise PersistError(
            "eval_runs insert failed",
            error_class=error_class,
            pgcode=pgcode,
        ) from exc
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()
    return run_id
