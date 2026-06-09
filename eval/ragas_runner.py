"""RAG quality eval runner — Ragas vs golden Q/A set.

Exit codes:
  0 — all gates pass (or skipped)
  1 — gate violation or error rate > 10%
  2 — schema/CLI error (bad golden set, missing --mode, invalid baseline)
  3 — workers unreachable at startup
  4 — persist failure (DB unreachable, constraint, timeout)
  5 — OIDC token fetch failure (authenticated target)
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from eval.persist import EvalRunRow

import httpx
import structlog
from pydantic import SecretStr

from eval.baseline_skip import should_skip_gate_b
from eval.clients import EntryError, GenerateClient, RetrieveClient
from eval.gates import apply_gate_a, apply_gate_b
from eval.loader import GoldenEntry, load_golden_set
from eval.metrics import (
    aggregate_breakdown,
    compute_context_recall_structural,
    compute_keyword_overlap,
    compute_ragas_metrics,
)
from eval.oidc import (
    IdTokenProvider,
    MetadataIdTokenProvider,
    OidcTokenError,
    derive_audience,
    is_authenticated_target,
)
from eval.run_writer import EntryResult, RunFile, RunTotals, write_run
from eval.stub_llm import RetrievedChunk as StubChunk
from eval.stub_llm import build_stub_answer

log = structlog.get_logger()

DEFAULT_GOLDEN_SET = Path("specs/golden_qa.jsonl")
DEFAULT_OUTPUT_DIR = Path("eval/runs")
ERROR_RATE_THRESHOLD = 0.10


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archiviste RAG eval runner")
    parser.add_argument(
        "--mode",
        choices=["live", "offline"],
        required=True,
        help="Evaluation mode (live|offline)",
    )
    parser.add_argument("--set", dest="golden_set", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--workers-url", default="http://localhost:8000")
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Persist aggregated metrics to Postgres eval_runs (live mode only)",
    )
    return parser.parse_args(argv)


def _get_git_sha(repo_path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(repo_path),
            check=True,
            timeout=5,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unknown"


def _check_workers_reachable(
    workers_url: str,
    auth_header: SecretStr | None = None,
) -> bool:
    headers: dict[str, str] = {}
    if auth_header is not None:
        headers["Authorization"] = f"Bearer {auth_header.get_secret_value()}"
    try:
        resp = httpx.get(f"{workers_url}/healthz", headers=headers, timeout=5.0)
        return resp.is_success
    except (httpx.HTTPError, OSError):
        return False


def _load_baseline(baseline_path: Path | None) -> dict[str, object] | None:
    if baseline_path is None:
        return None
    if not baseline_path.exists():
        return None
    try:
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
        return dict(data)
    except (json.JSONDecodeError, OSError) as exc:
        sys.stderr.write(f"invalid baseline schema at {baseline_path}: {exc}\n")
        sys.exit(2)


def _run_entry_offline(
    entry_result: EntryResult,
    retrieve_client: RetrieveClient,
) -> None:
    """Run a single entry in offline mode: real retrieve, stub generate."""
    response = retrieve_client.search(entry_result.question, entry_result.request_id)
    if isinstance(response, EntryError):
        entry_result.status = response.status
        return

    chunks = response.chunks
    entry_result.retrieved_contexts = [c.source_path for c in chunks]
    # Store chunk texts for keyword overlap check against retrieved content (not stub answer).
    entry_result.retrieved_chunk_texts = [c.text for c in chunks]
    stub_chunks = [StubChunk(source_path=c.source_path, text=c.text) for c in chunks]
    keywords = entry_result.ground_truth.split() if entry_result.ground_truth else []
    entry_result.answer = build_stub_answer(keywords, stub_chunks)
    entry_result.status = "ok"


def _run_entry_live(
    entry_result: EntryResult,
    retrieve_client: RetrieveClient,
    generate_client: GenerateClient,
) -> None:
    """Run a single entry in live mode: real retrieve + real generate."""
    retrieve_response = retrieve_client.search(
        entry_result.question, entry_result.request_id
    )
    if isinstance(retrieve_response, EntryError):
        entry_result.status = retrieve_response.status
        return

    chunks = retrieve_response.chunks
    entry_result.retrieved_contexts = [c.source_path for c in chunks]

    generate_response = generate_client.generate(
        entry_result.question,
        entry_result.request_id,
        contexts=[c.source_path for c in chunks],
    )
    if isinstance(generate_response, EntryError):
        entry_result.status = generate_response.status
        return

    entry_result.answer = generate_response.answer
    entry_result.citations = generate_response.citations
    entry_result.status = "ok"


def _compute_entry_metrics(
    entry_result: EntryResult,
    expected_keywords: list[str],
    expected_contexts: list[str],
    is_offline: bool,
) -> None:
    """Fill entry_result.metrics with deterministic metrics.

    In offline mode, keyword_overlap_rate is computed against retrieved chunk texts
    (not the stub answer) to avoid the tautological self-match: the stub answer
    always contains the keywords, making the metric a no-op. Measuring against
    chunk texts gives a genuine signal about retrieval quality.
    """
    if entry_result.status != "ok" or entry_result.answer is None:
        entry_result.metrics = {
            "keyword_overlap_rate": None,
            "context_recall_structural": None,
        }
        return

    if is_offline:
        # Check keywords against retrieved chunk texts to avoid self-match with stub answer.
        chunk_corpus = " ".join(entry_result.retrieved_chunk_texts)
        overlap = compute_keyword_overlap(chunk_corpus, expected_keywords)
    else:
        overlap = compute_keyword_overlap(entry_result.answer, expected_keywords)
    entry_result.metrics["keyword_overlap_rate"] = 1.0 if overlap else 0.0

    if entry_result.mode == "canon":
        recall = compute_context_recall_structural(
            expected_contexts,
            entry_result.retrieved_contexts,
        )
        entry_result.metrics["context_recall_structural"] = recall


def _build_run(
    runner_mode: Literal["live", "offline"],
    started_at: str,
    git_sha: str,
    totals: RunTotals,
    breakdown: dict[str, object],
    ragas_metrics: dict[str, float | None],
    entry_results: list[EntryResult],
    judge_identity: dict[str, str] | None = None,
) -> RunFile:
    finished_at = datetime.datetime.now(datetime.UTC).isoformat()
    return RunFile(
        mode=runner_mode,
        started_at=started_at,
        finished_at=finished_at,
        git_sha=git_sha,
        runner_mode=runner_mode,
        totals=totals,
        breakdown_by_mode=breakdown,
        metrics=ragas_metrics,
        entries=entry_results,
        judge=judge_identity,
    )


def _run_all_entries(
    golden_entries: list[GoldenEntry],
    runner_mode: Literal["live", "offline"],
    retrieve_client: RetrieveClient,
    generate_client: GenerateClient,
) -> tuple[list[EntryResult], int]:
    """Execute pipeline for all entries; return results and error count."""
    entry_results: list[EntryResult] = []
    error_count = 0
    is_offline = runner_mode == "offline"

    for golden in golden_entries:
        request_id = str(uuid.uuid4())
        entry = EntryResult(
            id=golden.id,
            mode=golden.mode,
            question=golden.question,
            status="ok",
            request_id=request_id,
            ground_truth=" ".join(golden.expected_answer_keywords),
        )
        if is_offline:
            _run_entry_offline(entry, retrieve_client)
        else:
            _run_entry_live(entry, retrieve_client, generate_client)
        _compute_entry_metrics(
            entry, golden.expected_answer_keywords, golden.expected_contexts, is_offline
        )
        if entry.status != "ok":
            error_count += 1
        log.info("eval_entry", id=entry.id, mode=entry.mode, status=entry.status)
        entry_results.append(entry)

    return entry_results, error_count


def _resolve_ragas_metrics(
    runner_mode: Literal["live", "offline"],
    entry_results: list[EntryResult],
) -> tuple[dict[str, float | None], dict[str, str] | None]:
    """Return (ragas_metrics, judge_identity) for live mode; null-filled/None for offline."""
    if runner_mode == "live":
        canon_ok = [e for e in entry_results if e.mode == "canon" and e.status == "ok"]
        return compute_ragas_metrics(canon_ok)
    return (
        {
            "faithfulness": None,
            "answer_relevancy": None,
            "context_precision": None,
            "context_recall": None,
        },
        None,
    )


def _handle_auto_create_baseline(
    baseline_path: Path,
    run: RunFile,
    runner_mode: str,
    ragas_metrics: dict[str, float | None],
    gate_a_passed: bool,
) -> int:
    """Write run as new baseline and emit PASS (no baseline yet). Returns exit code."""
    error_rate = run.totals.errors / run.totals.entries if run.totals.entries > 0 else 0.0
    if error_rate > ERROR_RATE_THRESHOLD:
        sys.stderr.write(
            f"cannot bootstrap baseline: error rate {error_rate * 100:.1f}% exceeds 10%\n"
        )
        return 1
    write_run(baseline_path, run)
    sys.stdout.write(
        json.dumps(
            {
                "event": "eval_summary",
                "mode_runner": runner_mode,
                "totals": {
                    "entries": run.totals.entries,
                    "ok": run.totals.ok,
                    "errors": run.totals.errors,
                },
                "metrics": ragas_metrics,
                "gate_a": {"passed": gate_a_passed},
                "gate_b": {"passed": True, "skipped": True, "skip_reason": "no baseline yet"},
                "verdict": "PASS (no baseline yet)",
            }
        )
        + "\n"
    )
    return 0


def _emit_summary_and_exit(
    runner_mode: str,
    run: RunFile,
    ragas_metrics: dict[str, float | None],
    breakdown: dict[str, object],
    baseline_run: dict[str, object] | None,
    repo_path: Path,
) -> int:
    """Apply gates, emit eval_summary JSON, return exit code."""
    gate_a = apply_gate_a(ragas_metrics, runner_mode)

    skip_gate_b = baseline_run is not None and should_skip_gate_b(repo_path)
    if skip_gate_b:
        log.info("gate_b_skipped", reason="baseline_bump_commit")

    effective_baseline = None if skip_gate_b else baseline_run
    gate_b = apply_gate_b(ragas_metrics, breakdown, effective_baseline, runner_mode)

    error_rate = run.totals.errors / run.totals.entries if run.totals.entries > 0 else 0.0
    error_rate_exceeded = error_rate > ERROR_RATE_THRESHOLD
    if error_rate_exceeded:
        sys.stderr.write(f"error rate {error_rate * 100:.1f}% exceeds 10% threshold\n")

    summary = {
        "event": "eval_summary",
        "mode_runner": runner_mode,
        "totals": {"entries": run.totals.entries, "ok": run.totals.ok, "errors": run.totals.errors},
        "metrics": ragas_metrics,
        "gate_a": {"passed": gate_a.passed, "violations": gate_a.violations},
        "gate_b": {
            "passed": gate_b.passed,
            "skipped": gate_b.skipped,
            "violations": gate_b.violations,
        },
    }
    sys.stdout.write(json.dumps(summary) + "\n")
    log.info("eval_summary", **{k: v for k, v in summary.items() if k != "event"})

    if not gate_a.passed or not gate_b.passed or error_rate_exceeded:
        return 1
    return 0


def _build_eval_run_row(run: RunFile, golden_set_version: str) -> EvalRunRow | None:
    """Build an EvalRunRow from a completed live RunFile; return None if any metric is absent."""
    from eval.persist import EvalRunRow  # noqa: PLC0415

    metrics = run.metrics
    faithfulness = metrics.get("faithfulness")
    answer_relevancy = metrics.get("answer_relevancy")
    context_precision = metrics.get("context_precision")
    context_recall = metrics.get("context_recall")
    if (
        faithfulness is None
        or answer_relevancy is None
        or context_precision is None
        or context_recall is None
    ):
        return None
    return EvalRunRow(
        git_sha=run.git_sha,
        runner_mode=run.runner_mode,
        golden_set_version=golden_set_version,
        faithfulness=faithfulness,
        answer_relevancy=answer_relevancy,
        context_precision=context_precision,
        context_recall=context_recall,
        entries_total=run.totals.entries,
        entries_ok=run.totals.ok,
        entries_errors=run.totals.errors,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _maybe_persist(
    args: argparse.Namespace,
    run: RunFile,
    runner_mode: Literal["live", "offline"],
) -> int | None:
    """Attempt to persist the run to Postgres; return 4 on failure, None on skip/success.

    None means "fall through to the normal EVAL-001 verdict flow" (AC-3, AC-5).
    """
    if not args.persist:
        return None

    if runner_mode == "offline":
        # Offline metrics are null/structural; NOT NULL columns forbid insertion (AC-5).
        log.info("persist_skipped", reason="offline_mode")
        return None

    from eval.persist import (  # noqa: PLC0415
        PersistError,
        derive_golden_set_version,
        persist_eval_run,
    )

    golden_set_version = derive_golden_set_version(args.golden_set)
    row = _build_eval_run_row(run, golden_set_version)
    if row is None:
        # Abnormal live run: one or more metrics are None; must not insert NULL (AC-11).
        log.warning("persist_failed")
        return 4
    try:
        run_id = persist_eval_run(row)
        log.info("persist_ok", id=run_id, golden_set_version=golden_set_version,
                 finished_at=run.finished_at)
    except PersistError:
        log.warning("persist_failed")
        return 4
    return None


def main(
    argv: list[str] | None = None,
    provider: IdTokenProvider | None = None,
) -> int:
    """CLI entry point. Returns exit code.

    The optional *provider* parameter is a test seam (D-4): when None the real
    MetadataIdTokenProvider is used; tests inject a fake to avoid metadata calls.
    AC-3: no new CLI flag/env — auth decision derived from --workers-url only.
    """
    args = _parse_args(argv)
    runner_mode: Literal["live", "offline"] = args.mode
    workers_url: str = args.workers_url
    started_at = datetime.datetime.now(datetime.UTC).isoformat()
    repo_path = Path(__file__).parent.parent

    log.info("eval_start", mode=runner_mode, golden_set=str(args.golden_set))

    try:
        golden_entries = load_golden_set(args.golden_set)
    except ValueError as exc:
        sys.stderr.write(f"schema error: {exc}\n")
        return 2

    # AC-6, D-3: fetch OIDC token fail-fast before any entry or healthz probe
    auth_header: SecretStr | None = None
    if is_authenticated_target(workers_url):
        audience = derive_audience(workers_url)
        effective_provider: IdTokenProvider = (
            provider if provider is not None else MetadataIdTokenProvider()
        )
        try:
            auth_header = effective_provider.fetch(audience)
        except OidcTokenError:
            # AC-7: log audience only — no token bytes
            log.warning("oidc_token_failed", audience=audience)
            return 5

    if not _check_workers_reachable(workers_url, auth_header):
        sys.stderr.write(f"workers unreachable at {workers_url}\n")
        return 3

    output_path = args.output or (
        DEFAULT_OUTPUT_DIR
        / f"{datetime.datetime.now(datetime.UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    baseline_run = _load_baseline(args.baseline)
    retrieve_client = RetrieveClient(base_url=workers_url, auth_header=auth_header)
    generate_client = GenerateClient(base_url=workers_url, auth_header=auth_header)

    entry_results, error_count = _run_all_entries(
        golden_entries, runner_mode, retrieve_client, generate_client
    )
    total_entries = len(entry_results)
    breakdown = aggregate_breakdown(entry_results)
    ragas_metrics, judge_identity = _resolve_ragas_metrics(runner_mode, entry_results)
    totals = RunTotals(entries=total_entries, ok=total_entries - error_count, errors=error_count)
    git_sha = _get_git_sha(repo_path)
    run = _build_run(
        runner_mode, started_at, git_sha, totals, breakdown, ragas_metrics, entry_results,
        judge_identity,
    )
    write_run(output_path, run)

    persist_exit = _maybe_persist(args, run, runner_mode)
    if persist_exit is not None:
        return persist_exit

    gate_a = apply_gate_a(ragas_metrics, runner_mode)
    if args.baseline is not None and baseline_run is None:
        return _handle_auto_create_baseline(
            args.baseline, run, runner_mode, ragas_metrics, gate_a.passed
        )

    return _emit_summary_and_exit(
        runner_mode, run, ragas_metrics, breakdown, baseline_run, repo_path
    )


if __name__ == "__main__":
    sys.exit(main())
