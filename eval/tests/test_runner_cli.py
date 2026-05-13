"""Integration CLI tests for eval/ragas_runner.py — AC-2, AC-9, AC-15, AC-16."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

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
    # Ensure eval/ package is importable when running from repo root
    python_path = env.get("PYTHONPATH", "")
    repo_root_str = str(REPO_ROOT)
    env["PYTHONPATH"] = f"{repo_root_str}{os.pathsep}{python_path}" if python_path else repo_root_str
    if env_extra:
        env.update(env_extra)

    result = subprocess.run(
        [sys.executable, RUNNER] + args,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    return result.returncode, result.stdout, result.stderr


# AC-2 : missing --mode → exit code 2
def test_missing_mode_exits_2() -> None:
    code, _stdout, stderr = _run_runner(["--set", str(FIXTURES / "golden_valid.jsonl")])
    assert code == 2


# AC-9 : --baseline pointing to non-existent file → auto-create + exit 0
def test_baseline_absent_auto_create(tmp_path: Path, httpserver: HTTPServer) -> None:
    httpserver.expect_request("/healthz").respond_with_json({"status": "ok"})
    httpserver.expect_request("/v1/retrieve").respond_with_json({
        "chunks": [{"source_path": "intro_p01", "text": "archiviste nocilia text here"}]
    })
    baseline_path = tmp_path / "new_baseline.json"
    output_path = tmp_path / "run.json"
    code, stdout, _stderr = _run_runner(
        [
            "--mode", "offline",
            "--set", str(FIXTURES / "golden_valid.jsonl"),
            "--baseline", str(baseline_path),
            "--output", str(output_path),
            "--workers-url", httpserver.url_for(""),
        ]
    )
    assert code == 0, f"exit={code} stdout={stdout}"
    assert "PASS (no baseline yet)" in stdout
    assert baseline_path.exists(), "baseline auto-created"


# AC-15 : error rate > 10% → exit code 1
def test_error_rate_exceeds_threshold_exits_1(tmp_path: Path, httpserver: HTTPServer) -> None:
    # Create a golden set with 3 entries (33% error rate > 10%)
    golden = tmp_path / "golden.jsonl"
    golden.write_text(
        '{"id": "q1", "mode": "canon", "question": "Q1?", "expected_contexts": [], "expected_answer_keywords": ["kw"]}\n'
        '{"id": "q2", "mode": "canon", "question": "Q2?", "expected_contexts": [], "expected_answer_keywords": ["kw"]}\n'
        '{"id": "q3", "mode": "canon", "question": "Q3?", "expected_contexts": [], "expected_answer_keywords": ["kw"]}\n',
        encoding="utf-8",
    )
    httpserver.expect_request("/healthz").respond_with_json({"status": "ok"})
    # Respond 500 for q1 (error), ok for others
    httpserver.expect_request("/v1/retrieve").respond_with_data("", status=500)

    output_path = tmp_path / "run.json"
    code, _stdout, stderr = _run_runner(
        [
            "--mode", "offline",
            "--set", str(golden),
            "--output", str(output_path),
            "--workers-url", httpserver.url_for(""),
        ]
    )
    assert code == 1, f"exit={code} stderr={stderr}"
    assert "error rate" in stderr.lower() or "10%" in stderr


# AC-16 : secrets not leaked in run file
def test_secrets_not_leaked_in_run_file(tmp_path: Path, httpserver: HTTPServer) -> None:
    fake_api_key = "sk-fakesecretkey12345"
    fake_db_url = "postgres://user:supersecretpwd@host/db"

    httpserver.expect_request("/healthz").respond_with_json({"status": "ok"})
    httpserver.expect_request("/v1/retrieve").respond_with_json({
        "chunks": [{"source_path": "intro_p01", "text": "some text"}]
    })
    output_path = tmp_path / "run.json"
    code, stdout, stderr = _run_runner(
        [
            "--mode", "offline",
            "--set", str(FIXTURES / "golden_valid.jsonl"),
            "--output", str(output_path),
            "--workers-url", httpserver.url_for(""),
        ],
        env_extra={
            "LLM_API_KEY": fake_api_key,
            "DATABASE_URL": fake_db_url,
        },
    )
    assert code in (0, 1), f"unexpected exit code {code}"
    if output_path.exists():
        run_content = output_path.read_text(encoding="utf-8")
        assert fake_api_key not in run_content, "API key leaked in run file"
        assert "supersecretpwd" not in run_content, "DB password leaked in run file"
    assert fake_api_key not in stdout, "API key leaked in stdout"
    assert fake_api_key not in stderr, "API key leaked in stderr"
    assert "supersecretpwd" not in stdout, "DB password leaked in stdout"
    assert "supersecretpwd" not in stderr, "DB password leaked in stderr"
