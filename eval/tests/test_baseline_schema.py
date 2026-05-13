"""Contract test: eval/baseline.json must conform to the RunFile AC-5 schema (HIGH-1)."""

from __future__ import annotations

import json
from pathlib import Path

BASELINE_PATH = Path(__file__).parent.parent / "baseline.json"

REQUIRED_TOP_LEVEL_KEYS = {
    "mode",
    "started_at",
    "finished_at",
    "git_sha",
    "runner_mode",
    "totals",
    "breakdown_by_mode",
    "metrics",
    "entries",
}

REQUIRED_TOTALS_KEYS = {"entries", "ok", "errors"}

REQUIRED_METRICS_KEYS = {
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
}

REQUIRED_BREAKDOWN_MODES = {"canon", "off_topic", "lore_gap", "mystery"}


def test_baseline_json_top_level_schema() -> None:
    """AC-17: baseline.json must have all top-level RunFile fields."""
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    missing = REQUIRED_TOP_LEVEL_KEYS - data.keys()
    assert not missing, f"baseline.json missing top-level keys: {missing}"


def test_baseline_json_totals_schema() -> None:
    """AC-17: baseline.json totals must have entries/ok/errors."""
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    totals = data["totals"]
    missing = REQUIRED_TOTALS_KEYS - totals.keys()
    assert not missing, f"baseline.json totals missing keys: {missing}"


def test_baseline_json_metrics_schema() -> None:
    """AC-17: baseline.json metrics must have all 4 Ragas metric keys."""
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    metrics = data["metrics"]
    missing = REQUIRED_METRICS_KEYS - metrics.keys()
    assert not missing, f"baseline.json metrics missing keys: {missing}"


def test_baseline_json_breakdown_by_mode_schema() -> None:
    """AC-17: baseline.json breakdown_by_mode must have all 4 mode keys."""
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    breakdown = data["breakdown_by_mode"]
    missing = REQUIRED_BREAKDOWN_MODES - breakdown.keys()
    assert not missing, f"baseline.json breakdown_by_mode missing modes: {missing}"


def test_baseline_json_canon_has_structural_metrics() -> None:
    """AC-17: baseline.json canon breakdown must have context_recall_structural."""
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    canon = data["breakdown_by_mode"]["canon"]
    assert "context_recall_structural" in canon, (
        "baseline.json canon breakdown must have context_recall_structural for gate B offline"
    )


def test_baseline_json_entries_is_list() -> None:
    """AC-17: baseline.json entries must be a list."""
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert isinstance(data["entries"], list), "baseline.json entries must be a list"
