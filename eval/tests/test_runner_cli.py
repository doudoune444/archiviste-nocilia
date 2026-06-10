"""Integration CLI tests for eval/ragas_runner.py — AC-2, AC-4, AC-9, AC-15, AC-16."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

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
    httpserver.expect_request("/health").respond_with_json({"status": "ok"})
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
    httpserver.expect_request("/health").respond_with_json({"status": "ok"})
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

    httpserver.expect_request("/health").respond_with_json({"status": "ok"})
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


# AC-16 property: 100 write_run calls with secrets actually injected into entry fields
# → sentinels must be redacted in output (positive assertion on [REDACTED] present).
def test_secrets_redaction_property_100_runs(tmp_path: Path) -> None:
    """AC-16 property: write_run must redact secrets that appear in serialised fields.

    The sentinels are injected into entry.answer (a field that IS serialised) so that
    _redact_raw has real work to do. If _redact_raw were commented out, the sentinel
    would appear in the file and the assertion would fail — no false confidence.
    """
    import datetime

    from eval.run_writer import EntryResult, RunFile, RunTotals, write_run

    # Use high-entropy sentinel strings that are clearly test fixtures, not real secrets.
    # Named without "key" suffix to avoid false positives from secret scanners.
    sentinel_llm_token = "test-llm-token-xyz9999-prop"
    sentinel_db_cred = "postgres://admin:prop-cred-fixture@dbhost/mydb"
    sentinel_password = "prop-cred-fixture"

    env_patch = {"LLM_API_KEY": sentinel_llm_token, "DATABASE_URL": sentinel_db_cred}

    for i in range(100):
        output_path = tmp_path / f"run_{i}.json"
        # Inject sentinels into serialised fields so _redact_raw actually has something to replace.
        # Vary placement across runs (answer / citations) to cover multiple field paths.
        if i % 3 == 0:
            answer_text = f"The api token is {sentinel_llm_token} according to the lore entry {i}."
            citations: list[str] = []
        elif i % 3 == 1:
            answer_text = f"Connection via {sentinel_db_cred} was established for entry {i}."
            citations = []
        else:
            answer_text = f"answer {i} without secret"
            citations = [f"source with {sentinel_llm_token} in path"]

        entry = EntryResult(
            id=f"e{i}",
            mode="canon",
            question="test?",
            status="ok",
            answer=answer_text,
            citations=citations,
            metrics={"keyword_overlap_rate": 1.0, "context_recall_structural": 0.5},
        )
        run = RunFile(
            mode="offline",
            started_at=datetime.datetime.now(datetime.UTC).isoformat(),
            finished_at=datetime.datetime.now(datetime.UTC).isoformat(),
            git_sha="abc",
            runner_mode="offline",
            totals=RunTotals(entries=1, ok=1, errors=0),
            breakdown_by_mode={"canon": {"entries": 1, "keyword_overlap_rate": 1.0}},
            metrics={
                "faithfulness": None, "answer_relevancy": None,
                "context_precision": None, "context_recall": None,
            },
            entries=[entry],
        )
        old_env = {k: os.environ.get(k) for k in env_patch}
        os.environ.update(env_patch)
        try:
            write_run(output_path, run)
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        content = output_path.read_text(encoding="utf-8")
        # Negative: sentinel must not appear in output.
        assert sentinel_llm_token not in content, f"run {i}: LLM token leaked in run file"
        assert sentinel_password not in content, f"run {i}: DB credential leaked in run file"
        # Positive: redaction marker must appear for runs that injected a sentinel in a field.
        if i % 3 != 2:
            assert "[REDACTED]" in content, f"run {i}: expected [REDACTED] marker absent"


# AC-4 property: two consecutive offline runs on same fixture produce byte-identical run files
# (modulo started_at / finished_at / git_sha).
def test_offline_double_run_byte_identical(tmp_path: Path, httpserver: HTTPServer) -> None:
    """AC-4: double offline run must produce SHA-256-identical payloads (deterministic)."""
    httpserver.expect_request("/health").respond_with_json({"status": "ok"})
    httpserver.expect_request("/v1/retrieve").respond_with_json({
        "chunks": [
            {"source_path": "intro_p01", "text": "archiviste nocilia intro text"},
            {"source_path": "intro_p02", "text": "more nocilia lore text"},
        ]
    })

    output1 = tmp_path / "run1.json"
    output2 = tmp_path / "run2.json"

    args = [
        "--mode", "offline",
        "--set", str(FIXTURES / "golden_valid.jsonl"),
        "--workers-url", httpserver.url_for(""),
    ]
    code1, _, _ = _run_runner(args + ["--output", str(output1)])
    code2, _, _ = _run_runner(args + ["--output", str(output2)])

    assert code1 in (0, 1), f"run1 must exit 0 or 1, got {code1}"
    assert code2 in (0, 1), f"run2 must exit 0 or 1, got {code2}"
    assert output1.exists(), "run1 file must be written"
    assert output2.exists(), "run2 file must be written"

    data1 = json.loads(output1.read_text(encoding="utf-8"))
    data2 = json.loads(output2.read_text(encoding="utf-8"))

    # Remove non-deterministic fields before comparing (per AC-4 spec: "modulo timestamps")
    for data in (data1, data2):
        data.pop("started_at", None)
        data.pop("finished_at", None)
        data.pop("git_sha", None)
        for entry in data.get("entries", []):
            # request_id is uuid4 per run — intentionally non-deterministic (tracing)
            entry.pop("request_id", None)

    canonical1 = json.dumps(data1, sort_keys=True, ensure_ascii=False)
    canonical2 = json.dumps(data2, sort_keys=True, ensure_ascii=False)

    hash1 = hashlib.sha256(canonical1.encode()).hexdigest()
    hash2 = hashlib.sha256(canonical2.encode()).hexdigest()

    assert hash1 == hash2, (
        f"Double offline run produced non-identical payloads.\n"
        f"SHA-256 run1={hash1}\nSHA-256 run2={hash2}"
    )
