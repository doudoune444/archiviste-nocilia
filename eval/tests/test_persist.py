"""Unit tests for eval/persist.py — AC-6, AC-7, AC-8, AC-9, AC-10, AC-11, EVAL-010.

AC-6: derive_golden_set_version determinism + 1-byte sensitivity.
AC-7: two persist_eval_run calls produce two INSERTs with distinct ids; no UPDATE/UPSERT.
AC-8: static grep of persist.py source confirms no f-string / .format( / %-interp on SQL.
AC-9: EvalRunRow has no answer/question/contexts/citations fields; params contain none.
AC-10: PersistError message never contains DATABASE_URL value or password substring.
AC-11: psycopg2.connect raising → PersistError raised → _maybe_persist returns 4.
EVAL-010: CLOUD_SQL_IAM_AUTH=true injects IAM token as psycopg2 password; token never logged.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from eval.persist import EvalRunRow, PersistError, derive_golden_set_version, persist_eval_run

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EVAL_DIR = Path(__file__).parent.parent
PERSIST_SOURCE = (EVAL_DIR / "persist.py").read_text(encoding="utf-8")


def _make_row() -> EvalRunRow:
    return EvalRunRow(
        git_sha="abc1234",
        runner_mode="live",
        golden_set_version="deadbeef",
        faithfulness=0.9,
        answer_relevancy=0.85,
        context_precision=0.8,
        context_recall=0.75,
        entries_total=4,
        entries_ok=4,
        entries_errors=0,
        started_at="2026-06-08T10:00:00+00:00",
        finished_at="2026-06-08T10:01:00+00:00",
    )


def _fake_connect(captured_calls: list[tuple[str, tuple[Any, ...]]]) -> Any:
    """Return a mock psycopg2 connection whose cursor captures (sql, params)."""
    cursor = MagicMock()

    def _execute(sql: str, params: tuple[Any, ...]) -> None:
        captured_calls.append((sql, params))

    cursor.execute.side_effect = _execute
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


# ---------------------------------------------------------------------------
# AC-6: derive_golden_set_version determinism + 1-byte sensitivity
# ---------------------------------------------------------------------------


def test_golden_set_version_determinism(tmp_path: Path) -> None:
    """AC-6: same bytes -> same SHA-256 hex digest."""
    content = b"golden set content line 1\ngolden set content line 2\n"
    golden_file = tmp_path / "golden.jsonl"
    golden_file.write_bytes(content)

    version_1 = derive_golden_set_version(golden_file)
    version_2 = derive_golden_set_version(golden_file)

    expected = hashlib.sha256(content).hexdigest()
    assert version_1 == expected
    assert version_1 == version_2


def test_golden_set_version_one_byte_mutation(tmp_path: Path) -> None:
    """AC-6: 1-byte change -> distinct digest."""
    content = b"golden set content line 1\n"
    mutated = b"golden set content line 2\n"
    assert content != mutated

    file_a = tmp_path / "a.jsonl"
    file_b = tmp_path / "b.jsonl"
    file_a.write_bytes(content)
    file_b.write_bytes(mutated)

    assert derive_golden_set_version(file_a) != derive_golden_set_version(file_b)


# ---------------------------------------------------------------------------
# AC-7: append-only — two calls -> two INSERTs with distinct id params
# ---------------------------------------------------------------------------


def test_two_persist_calls_produce_two_inserts(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-7: each persist_eval_run call issues exactly one INSERT with a unique id."""
    captured: list[tuple[str, tuple[Any, ...]]] = []
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")

    with patch("psycopg2.connect", return_value=_fake_connect(captured)):
        id_1 = persist_eval_run(_make_row())

    with patch("psycopg2.connect", return_value=_fake_connect(captured)):
        id_2 = persist_eval_run(_make_row())

    assert len(captured) == 2
    assert id_1 != id_2
    # params[0] is the id column
    assert captured[0][1][0] == id_1
    assert captured[1][1][0] == id_2


def test_insert_sql_contains_no_update_or_upsert() -> None:
    """AC-7: the SQL literal must be a plain INSERT with no ON CONFLICT / UPSERT."""
    from eval.persist import _INSERT_SQL

    sql_upper = _INSERT_SQL.upper()
    assert "UPDATE" not in sql_upper
    assert "ON CONFLICT" not in sql_upper
    assert "UPSERT" not in sql_upper


# ---------------------------------------------------------------------------
# AC-8: static grep — no f-string / .format( / %-interpolation on the SQL literal
# ---------------------------------------------------------------------------


def test_insert_sql_no_fstring_interpolation() -> None:
    """AC-8: the _INSERT_SQL string literal must not be built via f-string."""
    # An f-string literal in source starts with f" or f'
    assert 'f"' not in PERSIST_SOURCE or "_INSERT_SQL" not in PERSIST_SOURCE.split('f"')[0]
    # More reliable: scan lines that assign _INSERT_SQL
    sql_block_lines = [
        line for line in PERSIST_SOURCE.splitlines() if "_INSERT_SQL" in line or "%s" in line
    ]
    for line in sql_block_lines:
        stripped = line.lstrip()
        assert not stripped.startswith("f'"), f"f-string found near INSERT SQL: {line!r}"
        assert not stripped.startswith('f"'), f"f-string found near INSERT SQL: {line!r}"


def test_insert_sql_no_format_call() -> None:
    """AC-8: .format( must not appear anywhere in persist.py source."""
    assert ".format(" not in PERSIST_SOURCE


def test_insert_sql_params_passed_as_tuple(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-8: cursor.execute receives a tuple of bound params, not an interpolated string."""
    captured: list[tuple[str, tuple[Any, ...]]] = []
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")

    with patch("psycopg2.connect", return_value=_fake_connect(captured)):
        persist_eval_run(_make_row())

    assert len(captured) == 1
    sql, params = captured[0]
    # Params must be a tuple; SQL must contain only %s placeholders (not {}, not %r, etc.)
    assert isinstance(params, tuple)
    assert "%s" in sql
    assert "{" not in sql


# ---------------------------------------------------------------------------
# AC-9: no LLM payload fields in EvalRunRow or inserted params
# ---------------------------------------------------------------------------


def test_eval_run_row_has_no_llm_payload_fields() -> None:
    """AC-9: EvalRunRow dataclass must not define answer/question/contexts/citations."""
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(EvalRunRow)}
    forbidden = {"answer", "question", "contexts", "citations", "retrieved_contexts"}
    overlap = field_names & forbidden
    assert not overlap, f"LLM payload fields found in EvalRunRow: {overlap}"


def test_inserted_params_contain_no_llm_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-9: the params tuple passed to execute must not contain answer/contexts strings."""
    captured: list[tuple[str, tuple[Any, ...]]] = []
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")

    with patch("psycopg2.connect", return_value=_fake_connect(captured)):
        persist_eval_run(_make_row())

    _sql, params = captured[0]
    params_str = " ".join(str(p) for p in params)
    for forbidden in ("answer", "contexts", "citations", "retrieved_contexts"):
        assert forbidden not in params_str.lower(), (
            f"Forbidden LLM field '{forbidden}' found in insert params"
        )


# ---------------------------------------------------------------------------
# AC-10: PersistError message must not leak DATABASE_URL / password
# ---------------------------------------------------------------------------


def test_persist_error_does_not_leak_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-10: on connect failure, PersistError message must not contain DSN or password."""
    secret_dsn = "postgres://user:supersecretpassword@somehost/mydb"
    monkeypatch.setenv("DATABASE_URL", secret_dsn)

    with patch("psycopg2.connect", side_effect=Exception("connection refused")), pytest.raises(PersistError) as exc_info:
        persist_eval_run(_make_row())

    error_message = str(exc_info.value)
    assert "supersecretpassword" not in error_message
    assert "postgres://" not in error_message
    assert secret_dsn not in error_message


# ---------------------------------------------------------------------------
# AC-11: persist failure -> _maybe_persist returns 4
# ---------------------------------------------------------------------------


def test_maybe_persist_returns_4_on_persist_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-11: when persist_eval_run raises PersistError, _maybe_persist must return 4."""
    from eval.ragas_runner import _maybe_persist
    from eval.run_writer import RunFile, RunTotals

    # Minimal fake args with persist=True and a real golden_set path
    golden_file = tmp_path / "golden.jsonl"
    golden_file.write_bytes(b'{"id":"q1"}\n')

    args = argparse.Namespace(persist=True, golden_set=golden_file)

    run = RunFile(
        mode="live",
        started_at="2026-06-08T10:00:00+00:00",
        finished_at="2026-06-08T10:01:00+00:00",
        git_sha="abc",
        runner_mode="live",
        totals=RunTotals(entries=1, ok=1, errors=0),
        breakdown_by_mode={},
        metrics={
            "faithfulness": 0.9,
            "answer_relevancy": 0.8,
            "context_precision": 0.7,
            "context_recall": 0.6,
        },
        entries=[],
    )

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@127.0.0.1:1/db")

    with patch(
        "eval.persist.persist_eval_run",
        side_effect=PersistError("db down", error_class="OperationalError", pgcode=None),
    ):
        result = _maybe_persist(args, run, "live")

    assert result == 4


def test_maybe_persist_returns_none_when_persist_flag_absent(tmp_path: Path) -> None:
    """AC-3 / AC-11: without --persist, _maybe_persist returns None immediately."""
    from eval.ragas_runner import _maybe_persist
    from eval.run_writer import RunFile, RunTotals

    args = argparse.Namespace(persist=False, golden_set=tmp_path / "golden.jsonl")
    run = RunFile(
        mode="live",
        started_at="2026-06-08T10:00:00+00:00",
        finished_at="2026-06-08T10:01:00+00:00",
        git_sha="abc",
        runner_mode="live",
        totals=RunTotals(entries=1, ok=1, errors=0),
        breakdown_by_mode={},
        metrics={"faithfulness": None, "answer_relevancy": None,
                 "context_precision": None, "context_recall": None},
        entries=[],
    )

    result = _maybe_persist(args, run, "live")
    assert result is None


def test_maybe_persist_returns_none_for_offline_mode(tmp_path: Path) -> None:
    """AC-5: --persist + offline mode -> None returned, no DB access."""
    from eval.ragas_runner import _maybe_persist
    from eval.run_writer import RunFile, RunTotals

    golden_file = tmp_path / "golden.jsonl"
    golden_file.write_bytes(b'{"id":"q1"}\n')
    args = argparse.Namespace(persist=True, golden_set=golden_file)
    run = RunFile(
        mode="offline",
        started_at="2026-06-08T10:00:00+00:00",
        finished_at="2026-06-08T10:01:00+00:00",
        git_sha="abc",
        runner_mode="offline",
        totals=RunTotals(entries=1, ok=1, errors=0),
        breakdown_by_mode={},
        metrics={"faithfulness": None, "answer_relevancy": None,
                 "context_precision": None, "context_recall": None},
        entries=[],
    )

    result = _maybe_persist(args, run, "offline")
    assert result is None


def test_maybe_persist_returns_4_when_metrics_are_none(tmp_path: Path) -> None:
    """AC-11 failure mode: live run with None metrics must not insert NULL rows -> return 4."""
    from eval.ragas_runner import _maybe_persist
    from eval.run_writer import RunFile, RunTotals

    golden_file = tmp_path / "golden.jsonl"
    golden_file.write_bytes(b'{"id":"q1"}\n')
    args = argparse.Namespace(persist=True, golden_set=golden_file)
    run = RunFile(
        mode="live",
        started_at="2026-06-08T10:00:00+00:00",
        finished_at="2026-06-08T10:01:00+00:00",
        git_sha="abc",
        runner_mode="live",
        totals=RunTotals(entries=1, ok=1, errors=0),
        breakdown_by_mode={},
        metrics={
            "faithfulness": None,
            "answer_relevancy": 0.8,
            "context_precision": 0.7,
            "context_recall": 0.6,
        },
        entries=[],
    )

    result = _maybe_persist(args, run, "live")
    assert result == 4


# ---------------------------------------------------------------------------
# EVAL-010: Cloud SQL IAM auth — token injected as psycopg2 password
# ---------------------------------------------------------------------------


class _FakeCreds:
    """Minimal ADC credentials stub: .refresh() sets .token to a fixed value."""

    def __init__(self) -> None:
        self.token: str | None = None

    def refresh(self, request: object) -> None:
        self.token = "fake-iam-token"


def _make_fake_google_auth(fake_creds: _FakeCreds) -> Any:
    """Return a module-level patcher for google.auth.default."""
    return patch(
        "eval.persist.google.auth.default",
        return_value=(fake_creds, "test-project"),
    )


def test_iam_auth_injects_token_as_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """EVAL-010: when CLOUD_SQL_IAM_AUTH=true, psycopg2.connect is called with password=<token>."""
    monkeypatch.setenv("CLOUD_SQL_IAM_AUTH", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://sa%40project.iam@/db?host=/cloudsql/inst")

    fake_creds = _FakeCreds()
    connect_kwargs: list[dict[str, Any]] = []

    def _capture_connect(dsn: str, **kwargs: Any) -> Any:
        connect_kwargs.append({"dsn": dsn, **kwargs})
        conn = MagicMock()
        conn.cursor.return_value = MagicMock()
        return conn

    with (
        _make_fake_google_auth(fake_creds),
        patch("eval.persist.google.auth.transport.requests.Request", return_value=object()),
        patch("psycopg2.connect", side_effect=_capture_connect),
    ):
        persist_eval_run(_make_row())

    assert len(connect_kwargs) == 1
    assert connect_kwargs[0]["password"] == "fake-iam-token"


def test_no_iam_auth_no_password_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    """EVAL-010: without CLOUD_SQL_IAM_AUTH=true, psycopg2.connect has no password kwarg."""
    monkeypatch.delenv("CLOUD_SQL_IAM_AUTH", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")

    connect_kwargs: list[dict[str, Any]] = []

    def _capture_connect(dsn: str, **kwargs: Any) -> Any:
        connect_kwargs.append({"dsn": dsn, **kwargs})
        conn = MagicMock()
        conn.cursor.return_value = MagicMock()
        return conn

    with patch("psycopg2.connect", side_effect=_capture_connect):
        persist_eval_run(_make_row())

    assert len(connect_kwargs) == 1
    assert "password" not in connect_kwargs[0]


def test_iam_token_not_in_persist_error_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """EVAL-010: when psycopg2.connect raises after IAM token fetch, token does not appear
    in PersistError string repr (security.md §A09 — token is a credential, must not leak).
    """
    monkeypatch.setenv("CLOUD_SQL_IAM_AUTH", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://sa%40project.iam@/db?host=/cloudsql/inst")

    fake_creds = _FakeCreds()

    class _FakePsycopgError(Exception):
        pgcode = "28000"

    with (
        _make_fake_google_auth(fake_creds),
        patch("eval.persist.google.auth.transport.requests.Request", return_value=object()),
        patch("psycopg2.connect", side_effect=_FakePsycopgError("auth")),
        pytest.raises(PersistError) as exc_info,
    ):
        persist_eval_run(_make_row())

    err = exc_info.value
    assert err.error_class == "_FakePsycopgError"
    assert err.pgcode == "28000"
    # Token must never appear in the PersistError string
    assert "fake-iam-token" not in str(err)
    assert "fake-iam-token" not in repr(err)


def test_persist_error_carries_error_class_and_pgcode(monkeypatch: pytest.MonkeyPatch) -> None:
    """EVAL-010: PersistError carries error_class and pgcode from the underlying exception."""
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db")
    monkeypatch.delenv("CLOUD_SQL_IAM_AUTH", raising=False)

    class _FakeDBError(Exception):
        pgcode = "08006"

    with (
        patch("psycopg2.connect", side_effect=_FakeDBError("connection failure")),
        pytest.raises(PersistError) as exc_info,
    ):
        persist_eval_run(_make_row())

    err = exc_info.value
    assert err.error_class == "_FakeDBError"
    assert err.pgcode == "08006"
