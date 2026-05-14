"""Tests for eval/metrics.py — AC-7, AC-8."""

from __future__ import annotations

from eval.metrics import compute_context_recall_structural, compute_keyword_overlap


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
