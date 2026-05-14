"""Gate A (absolute, live only) and Gate B (no-regression, both modes)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

GATE_A_THRESHOLDS: dict[str, float] = {
    "faithfulness": 0.85,
    "answer_relevancy": 0.85,
    "context_precision": 0.70,
    "context_recall": 0.70,
}

GATE_B_TOLERANCES: dict[str, float] = {
    "faithfulness": 0.02,
    "answer_relevancy": 0.02,
    "context_precision": 0.03,
    "context_recall": 0.03,
}

GATE_B_OFFLINE_TOLERANCES: dict[str, float] = {
    "context_recall_structural": 0.05,
    "keyword_overlap_rate": 0.05,
}


@dataclass
class GateAResult:
    """Outcome of Gate A absolute check."""

    passed: bool
    violations: list[str] = field(default_factory=list)


@dataclass
class GateBResult:
    """Outcome of Gate B no-regression check."""

    passed: bool
    skipped: bool = False
    skip_reason: str = ""
    violations: list[str] = field(default_factory=list)


def apply_gate_a(
    metrics: dict[str, float | None],
    runner_mode: str,
) -> GateAResult:
    """Apply absolute gate (AC-10). Only enforced in live mode."""
    if runner_mode != "live":
        sys.stderr.write('{"event": "gate_a_skipped", "reason": "offline_mode"}\n')
        return GateAResult(passed=True)

    violations: list[str] = []
    for metric, threshold in GATE_A_THRESHOLDS.items():
        value = metrics.get(metric)
        if value is not None and value < threshold:
            violations.append(
                f"{metric} observed={value:.4f} threshold={threshold:.2f}"
            )

    for violation in violations:
        sys.stderr.write(f"gate_a_violation: {violation}\n")
    return GateAResult(passed=not violations, violations=violations)


def apply_gate_b(
    current_metrics: dict[str, float | None],
    current_breakdown: dict[str, Any],
    baseline_run: dict[str, Any] | None,
    runner_mode: str,
) -> GateBResult:
    """Apply no-regression gate (AC-11)."""
    if baseline_run is None:
        return GateBResult(passed=True, skipped=True, skip_reason="no baseline provided")

    violations: list[str] = []
    baseline_metrics: dict[str, Any] = baseline_run.get("metrics", {})

    for metric, tolerance in GATE_B_TOLERANCES.items():
        current_val = current_metrics.get(metric)
        baseline_val = baseline_metrics.get(metric)
        if current_val is None or baseline_val is None:
            sys.stderr.write(
                f'{{"event": "gate_b_metric_skipped", "reason": "null", "metric": "{metric}"}}\n'
            )
            continue
        delta = float(current_val) - float(baseline_val)
        if delta < -tolerance:
            violations.append(
                f"{metric} observed={current_val:.4f} baseline={baseline_val:.4f} "
                f"delta={delta:.4f} tolerance={tolerance:.2f}"
            )

    if runner_mode == "offline":
        violations.extend(
            _check_offline_structural_gates(current_breakdown, baseline_run)
        )

    for violation in violations:
        sys.stderr.write(f"gate_b_violation: {violation}\n")
    return GateBResult(passed=not violations, violations=violations)


def _check_offline_structural_gates(
    current_breakdown: dict[str, Any],
    baseline_run: dict[str, Any],
) -> list[str]:
    """Check deterministic offline gates for canon mode (AC-11 offline property-checks)."""
    violations: list[str] = []
    baseline_breakdown: dict[str, Any] = baseline_run.get("breakdown_by_mode", {})
    current_canon: dict[str, Any] = current_breakdown.get("canon", {})
    baseline_canon: dict[str, Any] = baseline_breakdown.get("canon", {})

    for metric, tolerance in GATE_B_OFFLINE_TOLERANCES.items():
        current_val = current_canon.get(metric)
        baseline_val = baseline_canon.get(metric)
        if current_val is None or baseline_val is None:
            sys.stderr.write(
                f'{{"event": "gate_b_metric_skipped", "reason": "null", "metric": "{metric}"}}\n'
            )
            continue
        delta = float(current_val) - float(baseline_val)
        if delta < -tolerance:
            violations.append(
                f"{metric} observed={current_val:.4f} baseline={baseline_val:.4f} "
                f"delta={delta:.4f} tolerance={tolerance:.2f}"
            )
    return violations
