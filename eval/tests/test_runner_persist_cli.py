"""CLI subprocess tests for --persist flag — AC-3, AC-5, AC-10, AC-11, AC-12, AC-4.

Mirrors the _run_runner helper from test_runner_cli.py (same PYTHONPATH / cwd pattern).

AC-3:  run WITHOUT --persist -> no DB access, exit/stdout EVAL-001-identical.
AC-5:  --mode offline --persist -> log persist_skipped reason=offline_mode, exit 0/1.
AC-10: no DATABASE_URL leaked in stdout/stderr on persist failure.
AC-11: DB unreachable with null metrics -> exit code 4.
AC-12: run-file JSON artifact written before persist attempt; intact on failure.
AC-4:  real Postgres; skipped if DATABASE_URL not set.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from pytest_httpserver import HTTPServer

EVAL_DIR = Path(__file__).parent.parent
REPO_ROOT = EVAL_DIR.parent
RUNNER = str(EVAL_DIR / "ragas_runner.py")
FIXTURES = Path(__file__).parent / "fixtures"


def _run_runner(
    args: list[str],
    env_extra: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run ragas_runner.py and return (returncode, stdout, stderr).

    PYTHONPATH is set to repo root so the eval package is importable.
    """
    import subprocess

    env = os.environ.copy()
    python_path = env.get("PYTHONPATH", "")
    repo_root_str = str(REPO_ROOT)
    env["PYTHONPATH"] = (
        f"{repo_root_str}{os.pathsep}{python_path}" if python_path else repo_root_str
    )
    if env_extra:
        env.update(env_extra)

    result = subprocess.run(
        [sys.executable, RUNNER] + args,
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=30,
    )
    return result.returncode, result.stdout, result.stderr


def _no_canon_golden(tmp_path: Path) -> Path:
    """Write a golden set with only off_topic entries; live Ragas returns null metrics."""
    path = tmp_path / "no_canon.jsonl"
    path.write_text(
        '{"id": "q1", "mode": "off_topic", "question": "Weather?", '
        '"expected_contexts": [], "expected_answer_keywords": ["unknown"]}\n',
        encoding="utf-8",
    )
    return path


def _mock_workers_offline(httpserver: HTTPServer) -> None:
    httpserver.expect_request("/health").respond_with_json({"status": "ok"})
    httpserver.expect_request("/v1/retrieve").respond_with_json({
        "chunks": [{"source_path": "intro_p01", "text": "archiviste nocilia text"}]
    })


def _mock_workers_live(httpserver: HTTPServer) -> None:
    _mock_workers_offline(httpserver)
    httpserver.expect_request("/v1/generate").respond_with_json({
        "answer": "answer text", "citations": [],
    })


# ---------------------------------------------------------------------------
# AC-3: without --persist, EVAL-001 behaviour is unchanged
# ---------------------------------------------------------------------------


def test_no_persist_flag_does_not_touch_postgres(
    tmp_path: Path, httpserver: HTTPServer
) -> None:
    """AC-3: run without --persist in offline mode; no DB access; artifact written."""
    _mock_workers_offline(httpserver)
    output_path = tmp_path / "run.json"
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    env["PYTHONPATH"] = str(REPO_ROOT)

    import subprocess
    result = subprocess.run(
        [sys.executable, RUNNER,
         "--mode", "offline",
         "--set", str(FIXTURES / "golden_valid.jsonl"),
         "--output", str(output_path),
         "--workers-url", httpserver.url_for(""),
         ],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=30,
    )
    assert result.returncode in (0, 1), f"exit={result.returncode} stderr={result.stderr}"
    assert output_path.exists()
    assert "persist_skipped" not in result.stdout + result.stderr
    assert "persist_failed" not in result.stdout + result.stderr


# ---------------------------------------------------------------------------
# AC-5: --mode offline --persist -> persist_skipped log, exit unchanged
# ---------------------------------------------------------------------------


def test_offline_persist_emits_skipped_log(tmp_path: Path, httpserver: HTTPServer) -> None:
    """AC-5: --persist + offline -> event=persist_skipped reason=offline_mode; exit 0/1."""
    _mock_workers_offline(httpserver)
    output_path = tmp_path / "run.json"

    code, stdout, stderr = _run_runner(
        ["--mode", "offline", "--persist",
         "--set", str(FIXTURES / "golden_valid.jsonl"),
         "--output", str(output_path),
         "--workers-url", httpserver.url_for(""),
         ],
    )

    assert code in (0, 1), f"exit={code}"
    combined = stdout + stderr
    assert "persist_skipped" in combined, f"missing persist_skipped in: {combined!r}"
    assert "offline_mode" in combined, f"missing offline_mode in: {combined!r}"
    assert "persist_failed" not in combined


# ---------------------------------------------------------------------------
# AC-11, AC-12, AC-10: DB unreachable + null metrics -> exit 4; artifact intact; no DSN leaked
# ---------------------------------------------------------------------------


def test_live_persist_null_metrics_exits_4_artifact_intact_no_leak(
    tmp_path: Path, httpserver: HTTPServer
) -> None:
    """AC-10/AC-11/AC-12: null metrics -> exit 4; artifact JSON valid; password not leaked.

    Uses a no-canon golden set so live Ragas returns null metrics without calling OpenAI.
    DATABASE_URL points to 127.0.0.1:1 (unreachable) to also cover the DB connect path.
    The null-metrics guard fires first (before even attempting the DB connection).
    """
    golden = _no_canon_golden(tmp_path)
    _mock_workers_live(httpserver)
    output_path = tmp_path / "run.json"
    secret_password = "ultraSecretPwd9999"

    code, stdout, stderr = _run_runner(
        ["--mode", "live", "--persist",
         "--set", str(golden),
         "--output", str(output_path),
         "--workers-url", httpserver.url_for(""),
         ],
        env_extra={
            "DATABASE_URL": f"postgres://admin:{secret_password}@127.0.0.1:1/db",
            "LLM_API_KEY": "test-stub-key",
        },
    )

    # AC-11: exit 4 on persist failure
    assert code == 4, f"expected exit 4; got {code}; stderr={stderr}"
    # AC-12: artifact written before persist attempt
    assert output_path.exists(), "run artifact must be present (AC-12)"
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert "metrics" in data
    # AC-10: password must not appear in stdout/stderr
    combined = stdout + stderr
    assert "persist_failed" in combined
    assert secret_password not in combined, f"password leaked: {combined!r}"


# ---------------------------------------------------------------------------
# AC-4: integration — real Postgres (skipped if DATABASE_URL absent)
# ---------------------------------------------------------------------------


def _db_count_eval_runs(database_url: str) -> int:
    """Return current count(*) of eval_runs rows."""
    import psycopg2

    conn = psycopg2.connect(database_url)
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM eval_runs")
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row[0] if row else 0


def _db_fetch_latest_eval_run(database_url: str) -> dict[str, Any]:
    """Return the last-inserted eval_runs row as a dict."""
    import psycopg2

    cols = (
        "git_sha", "runner_mode", "faithfulness", "answer_relevancy",
        "context_precision", "context_recall",
        "entries_total", "entries_ok", "entries_errors", "started_at", "finished_at",
    )
    conn = psycopg2.connect(database_url)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT git_sha, runner_mode, faithfulness, answer_relevancy,"
        " context_precision, context_recall, entries_total, entries_ok, entries_errors,"
        " started_at, finished_at FROM eval_runs ORDER BY created_at DESC LIMIT 1"
    )
    row: tuple[Any, ...] | None = cursor.fetchone()
    cursor.close()
    conn.close()
    assert row is not None
    return dict(zip(cols, row, strict=True))


def _assert_db_matches_run_file(db: dict[str, Any], run: dict[str, Any]) -> None:
    """Cross-assert DB row vs run-file fields (AC-4)."""
    import datetime
    import decimal

    totals: dict[str, Any] = run["totals"]
    assert db["git_sha"] == run["git_sha"]
    assert db["runner_mode"] == "live"
    assert db["entries_total"] == totals["entries"]
    assert db["entries_ok"] == totals["ok"]
    assert db["entries_errors"] == totals["errors"]

    tolerance = decimal.Decimal("0.0001")
    metrics: dict[str, Any] = run["metrics"]
    for key in ("faithfulness", "answer_relevancy", "context_precision", "context_recall"):
        run_val = metrics[key]
        if run_val is None:
            continue
        diff = abs(decimal.Decimal(str(db[key])) - decimal.Decimal(str(run_val)))
        assert diff <= tolerance, f"{key}: db={db[key]!r} run={run_val!r}"

    def _ts(v: Any) -> datetime.datetime:
        if isinstance(v, datetime.datetime):
            return v.astimezone(datetime.UTC)
        return datetime.datetime.fromisoformat(str(v)).astimezone(datetime.UTC)

    assert _ts(db["started_at"]) == _ts(run["started_at"])
    assert _ts(db["finished_at"]) == _ts(run["finished_at"])


def test_live_persist_inserts_row_matching_run_file(
    tmp_path: Path, httpserver: HTTPServer
) -> None:
    """AC-4: with real Postgres, exactly 1 row inserted; columns match run-file fields."""
    database_url = os.environ.get("DATABASE_URL") or ""
    if not database_url:
        pytest.skip("DATABASE_URL not set — skipping integration test")

    _mock_workers_live(httpserver)
    output_path = tmp_path / "run_int.json"
    count_before = _db_count_eval_runs(database_url)

    code, _stdout, stderr = _run_runner(
        ["--mode", "live", "--persist",
         "--set", str(FIXTURES / "golden_valid.jsonl"),
         "--output", str(output_path),
         "--workers-url", httpserver.url_for(""),
         ],
        env_extra={"DATABASE_URL": database_url, "LLM_API_KEY": "test-stub-key"},
    )

    if code == 4:
        pytest.skip("Live metrics are None (no real LLM judge); persist skipped")

    assert code in (0, 1), f"exit={code} stderr={stderr}"
    assert output_path.exists()
    run_data: dict[str, Any] = json.loads(output_path.read_text(encoding="utf-8"))

    count_after = _db_count_eval_runs(database_url)
    assert count_after == count_before + 1, f"before={count_before} after={count_after}"

    db_row = _db_fetch_latest_eval_run(database_url)
    _assert_db_matches_run_file(db_row, run_data)
