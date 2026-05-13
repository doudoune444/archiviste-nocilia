"""RAG quality eval runner — Ragas vs golden Q/A set.

Exit codes:
  0 — all gates pass (or skipped)
  1 — gate violation or error rate > 10%
  2 — schema/CLI error (bad golden set, missing --mode, invalid baseline)
  3 — workers unreachable at startup
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Literal

import httpx
import structlog

from eval.baseline_skip import should_skip_gate_b
from eval.clients import EntryError, GenerateClient, RetrieveClient
from eval.gates import apply_gate_a, apply_gate_b
from eval.loader import load_golden_set
from eval.metrics import (
    aggregate_breakdown,
    compute_context_recall_structural,
    compute_keyword_overlap,
    compute_ragas_metrics,
)
from eval.run_writer import EntryResult, RunFile, RunTotals, write_run
from eval.stub_llm import RetrievedChunk as StubChunk
from eval.stub_llm import build_stub_answer

log = structlog.get_logger()

DEFAULT_GOLDEN_SET = Path("specs/golden_qa.jsonl")
DEFAULT_OUTPUT_DIR = Path("eval/runs")


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
    except Exception:
        return "unknown"


def _check_workers_reachable(workers_url: str) -> bool:
    try:
        resp = httpx.get(f"{workers_url}/healthz", timeout=5.0)
        return resp.is_success
    except Exception:
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
) -> None:
    """Fill entry_result.metrics with deterministic metrics."""
    if entry_result.status != "ok" or entry_result.answer is None:
        entry_result.metrics = {
            "keyword_overlap_rate": None,
            "context_recall_structural": None,
        }
        return

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
    )


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915
    """CLI entry point. Returns exit code."""
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

    if not _check_workers_reachable(workers_url):
        sys.stderr.write(f"workers unreachable at {workers_url}\n")
        return 3

    output_path = args.output or (
        DEFAULT_OUTPUT_DIR
        / f"{datetime.datetime.now(datetime.UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )

    baseline_run = _load_baseline(args.baseline)

    retrieve_client = RetrieveClient(base_url=workers_url)
    generate_client = GenerateClient(base_url=workers_url)

    entry_results: list[EntryResult] = []
    error_count = 0

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

        if runner_mode == "offline":
            _run_entry_offline(entry, retrieve_client)
        else:
            _run_entry_live(entry, retrieve_client, generate_client)

        _compute_entry_metrics(
            entry, golden.expected_answer_keywords, golden.expected_contexts
        )

        if entry.status != "ok":
            error_count += 1

        log.info("eval_entry", id=entry.id, mode=entry.mode, status=entry.status)
        entry_results.append(entry)

    total_entries = len(entry_results)
    ok_count = total_entries - error_count
    breakdown = aggregate_breakdown(entry_results)

    canon_ok_entries = [e for e in entry_results if e.mode == "canon" and e.status == "ok"]
    if runner_mode == "live":
        ragas_metrics = compute_ragas_metrics(canon_ok_entries)
    else:
        ragas_metrics = {
            "faithfulness": None,
            "answer_relevancy": None,
            "context_precision": None,
            "context_recall": None,
        }

    totals = RunTotals(entries=total_entries, ok=ok_count, errors=error_count)
    git_sha = _get_git_sha(repo_path)
    run = _build_run(
        runner_mode, started_at, git_sha, totals, breakdown, ragas_metrics, entry_results
    )
    write_run(output_path, run)

    gate_a = apply_gate_a(ragas_metrics, runner_mode)

    if args.baseline is not None and baseline_run is None:
        write_run(args.baseline, run)
        verdict = "PASS (no baseline yet)"
        sys.stdout.write(
            json.dumps(
                {
                    "event": "eval_summary",
                    "mode_runner": runner_mode,
                    "totals": {
                        "entries": total_entries,
                        "ok": ok_count,
                        "errors": error_count,
                    },
                    "metrics": ragas_metrics,
                    "gate_a": {"passed": gate_a.passed},
                    "gate_b": {
                        "passed": True,
                        "skipped": True,
                        "skip_reason": "no baseline yet",
                    },
                    "verdict": verdict,
                }
            )
            + "\n"
        )
        return 0

    skip_gate_b = baseline_run is not None and should_skip_gate_b(repo_path)
    if skip_gate_b:
        log.info("gate_b_skipped", reason="baseline_bump_commit")

    effective_baseline = None if skip_gate_b else baseline_run
    gate_b = apply_gate_b(ragas_metrics, breakdown, effective_baseline, runner_mode)

    error_rate = error_count / total_entries if total_entries > 0 else 0.0
    error_rate_threshold = 0.10
    error_rate_exceeded = error_rate > error_rate_threshold
    if error_rate_exceeded:
        sys.stderr.write(
            f"error rate {error_rate * 100:.1f}% exceeds 10% threshold\n"
        )

    summary = {
        "event": "eval_summary",
        "mode_runner": runner_mode,
        "totals": {"entries": total_entries, "ok": ok_count, "errors": error_count},
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


if __name__ == "__main__":
    sys.exit(main())
