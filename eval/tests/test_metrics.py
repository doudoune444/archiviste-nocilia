"""Tests for eval/metrics.py — AC-7, AC-8."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from eval.metrics import (
    RAGAS_MAX_WORKERS,
    compute_context_recall_structural,
    compute_keyword_overlap,
)

_FAKE_API_KEY = "sk-secret-test-key-do-not-log"


# AC-7 : keyword_overlap case-insensitive substring match
def test_keyword_overlap_case_insensitive() -> None:
    assert compute_keyword_overlap("The Archiviste exists", ["archiviste"]) is True


# AC-7 : at least one keyword match → True
def test_keyword_overlap_partial_match() -> None:
    assert compute_keyword_overlap("hello world", ["missing", "world"]) is True


# AC-7 : no keyword match → False
def test_keyword_overlap_no_match() -> None:
    assert compute_keyword_overlap("hello world", ["nocilia", "archiviste"]) is False


# AC-7 : empty keywords → False (no keyword can match)
def test_keyword_overlap_empty_keywords() -> None:
    assert compute_keyword_overlap("anything", []) is False


# AC-8 : context recall structural 0.5 = 1 of 2 expected found
def test_context_recall_structural_half() -> None:
    result = compute_context_recall_structural(
        expected_contexts=["intro_p01", "intro_p02"],
        retrieved_contexts=["intro_p01", "other_p03"],
    )
    assert result == 0.5


# AC-8 : all expected found → 1.0
def test_context_recall_structural_full() -> None:
    result = compute_context_recall_structural(
        expected_contexts=["p01", "p02"],
        retrieved_contexts=["p01", "p02", "p03"],
    )
    assert result == 1.0


# AC-8 : none found → 0.0
def test_context_recall_structural_none_found() -> None:
    result = compute_context_recall_structural(
        expected_contexts=["p01"],
        retrieved_contexts=["p99"],
    )
    assert result == 0.0


# AC-8 : empty expected → 0.0
def test_context_recall_structural_empty_expected() -> None:
    result = compute_context_recall_structural(
        expected_contexts=[],
        retrieved_contexts=["p01"],
    )
    assert result == 0.0


# EVAL-008: ragas.evaluate must be called with run_config.max_workers == RAGAS_MAX_WORKERS (3)
# so 16 concurrent Mistral calls are throttled to 3, preventing HTTP 429 storms.
def test_run_ragas_evaluate_passes_run_config_max_workers() -> None:
    """EVAL-008: _run_ragas_evaluate passes run_config with max_workers=3 to ragas.evaluate().

    Ragas defaults to max_workers=16 which floods Mistral with concurrent requests →
    HTTP 429 storms → retry backoff exceeds the Cloud Run Job task timeout.
    """
    import pandas as pd

    from eval.metrics import _run_ragas_evaluate
    from eval.run_writer import EntryResult

    entries = [
        EntryResult(
            id="q1",
            mode="canon",
            question="What is Nocilia?",
            status="ok",
            answer="A city.",
            ground_truth="city",
            retrieved_contexts=["doc/intro.md"],
        )
    ]

    captured_kwargs: dict[str, Any] = {}

    def fake_evaluate(dataset: Any, metrics: Any = None, **kwargs: Any) -> Any:
        captured_kwargs.update(kwargs)
        mock_result = MagicMock()
        mock_result.to_pandas.return_value = pd.DataFrame(
            {
                "faithfulness": [0.9],
                "answer_relevancy": [0.85],
                "context_precision": [0.8],
                "context_recall": [0.75],
            }
        )
        return mock_result

    with (
        patch.dict(os.environ, {"RAGAS_JUDGE_PROVIDER": "mistral", "LLM_API_KEY": _FAKE_API_KEY}),
        patch("ragas.evaluate", fake_evaluate),
    ):
        _run_ragas_evaluate(entries)

    run_config = captured_kwargs.get("run_config")
    assert run_config is not None, "ragas.evaluate must be called with run_config="
    assert run_config.max_workers == RAGAS_MAX_WORKERS, (
        f"run_config.max_workers must be {RAGAS_MAX_WORKERS} to throttle Mistral calls; "
        f"got {run_config.max_workers}"
    )
