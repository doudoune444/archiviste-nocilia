"""Tests for eval/gates.py — AC-10, AC-11, AC-12."""

from __future__ import annotations

from typing import Any

from eval.gates import GateAResult, GateBResult, apply_gate_a, apply_gate_b


def _metrics(**kwargs: float | None) -> dict[str, float | None]:
    base: dict[str, float | None] = {
        "faithfulness": None,
        "answer_relevancy": None,
        "context_precision": None,
        "context_recall": None,
    }
    base.update(kwargs)
    return base


def _baseline_run(metrics: dict[str, float | None], canon: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "metrics": metrics,
        "breakdown_by_mode": {"canon": canon or {}},
    }


# AC-10 : gate A live mode — violation when faithfulness < 0.85
def test_gate_a_live_faithfulness_violation() -> None:
    metrics = _metrics(faithfulness=0.80, answer_relevancy=0.90, context_precision=0.75, context_recall=0.75)
    result = apply_gate_a(metrics, runner_mode="live")
    assert not result.passed
    assert any("faithfulness" in v for v in result.violations)
    assert any("observed=0.8000" in v or "observed=0.80" in v for v in result.violations)


# AC-10 : gate A live mode — all pass
def test_gate_a_live_all_pass() -> None:
    metrics = _metrics(faithfulness=0.90, answer_relevancy=0.90, context_precision=0.75, context_recall=0.75)
    result = apply_gate_a(metrics, runner_mode="live")
    assert result.passed
    assert result.violations == []


# AC-10 : gate A skipped in offline mode
def test_gate_a_skipped_offline() -> None:
    metrics = _metrics(faithfulness=0.50, answer_relevancy=0.50, context_precision=0.40, context_recall=0.40)
    result = apply_gate_a(metrics, runner_mode="offline")
    assert result.passed
    assert result.violations == []


# AC-11 : gate B live — drop within tolerance → pass (exit code 0)
def test_gate_b_live_drop_within_tolerance() -> None:
    current = _metrics(faithfulness=0.89, answer_relevancy=0.90, context_precision=0.75, context_recall=0.75)
    baseline = _baseline_run(_metrics(faithfulness=0.90, answer_relevancy=0.91, context_precision=0.76, context_recall=0.76))
    result = apply_gate_b(current, {}, baseline, runner_mode="live")
    assert result.passed


# AC-11 : gate B live — faithfulness drop > 0.02 → violation
def test_gate_b_live_faithfulness_drop_exceeds_tolerance() -> None:
    current = _metrics(faithfulness=0.87, answer_relevancy=0.90, context_precision=0.75, context_recall=0.75)
    baseline = _baseline_run(_metrics(faithfulness=0.90, answer_relevancy=0.90, context_precision=0.75, context_recall=0.75))
    result = apply_gate_b(current, {}, baseline, runner_mode="live")
    assert not result.passed
    assert any("faithfulness" in v for v in result.violations)


# AC-11 : gate B offline — context_recall_structural drop > 0.05 → violation
def test_gate_b_offline_context_recall_structural_violation() -> None:
    current_metrics = _metrics()
    current_breakdown: dict[str, Any] = {"canon": {"context_recall_structural": 0.74, "keyword_overlap_rate": 0.90}}
    baseline = _baseline_run(_metrics(), {"context_recall_structural": 0.80, "keyword_overlap_rate": 0.90})
    result = apply_gate_b(current_metrics, current_breakdown, baseline, runner_mode="offline")
    assert not result.passed
    assert any("context_recall_structural" in v for v in result.violations)


# AC-11 : gate B offline — keyword_overlap_rate drop > 0.05 → violation
def test_gate_b_offline_keyword_overlap_rate_violation() -> None:
    current_metrics = _metrics()
    current_breakdown: dict[str, Any] = {"canon": {"context_recall_structural": 0.80, "keyword_overlap_rate": 0.84}}
    baseline = _baseline_run(_metrics(), {"context_recall_structural": 0.80, "keyword_overlap_rate": 0.90})
    result = apply_gate_b(current_metrics, current_breakdown, baseline, runner_mode="offline")
    assert not result.passed
    assert any("keyword_overlap_rate" in v for v in result.violations)


# AC-12 : no violations → exit code 0 (GateBResult.passed = True, GateAResult.passed = True)
def test_gates_pass_returns_passed_results() -> None:
    gate_a = apply_gate_a(
        _metrics(faithfulness=0.90, answer_relevancy=0.90, context_precision=0.75, context_recall=0.75),
        runner_mode="live",
    )
    gate_b = apply_gate_b(_metrics(), {}, None, runner_mode="live")
    assert isinstance(gate_a, GateAResult)
    assert isinstance(gate_b, GateBResult)
    assert gate_a.passed
    assert gate_b.passed
    assert gate_b.skipped


# AC-11 : no baseline → gate B skipped
def test_gate_b_no_baseline_skipped() -> None:
    result = apply_gate_b(_metrics(), {}, None, runner_mode="live")
    assert result.passed
    assert result.skipped
