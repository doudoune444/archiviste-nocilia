"""Tests for eval/loader.py — AC-1."""

from __future__ import annotations

from pathlib import Path

import pytest

from eval.loader import GoldenEntry, load_golden_set

FIXTURES = Path(__file__).parent / "fixtures"


# AC-1 : valid entries load without error
def test_load_valid_golden_set() -> None:
    entries = load_golden_set(FIXTURES / "golden_valid.jsonl")
    assert len(entries) == 4
    assert all(isinstance(e, GoldenEntry) for e in entries)


# AC-1 : mode invalid → error cites id + field
def test_load_invalid_mode_cites_id() -> None:
    with pytest.raises(ValueError, match="q001"):
        load_golden_set(FIXTURES / "golden_invalid_mode.jsonl")


# AC-1 : empty id → error raised, no entry accepted
def test_load_empty_id_raises() -> None:
    with pytest.raises(ValueError):
        load_golden_set(FIXTURES / "golden_invalid_id.jsonl")


# AC-1 : expected_contexts non-list → error
def test_load_expected_contexts_not_list(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        '{"id": "q1", "mode": "canon", "question": "x", "expected_contexts": "not_a_list", "expected_answer_keywords": []}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="q1"):
        load_golden_set(bad)


# AC-1 : expected_answer_keywords non-list → error
def test_load_keywords_not_list(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        '{"id": "q2", "mode": "canon", "question": "x", "expected_contexts": [], "expected_answer_keywords": "keyword"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="q2"):
        load_golden_set(bad)


# AC-1 : extra field forbidden
def test_load_extra_field_forbidden(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        '{"id": "q3", "mode": "canon", "question": "x", "expected_contexts": [], "expected_answer_keywords": [], "unknown_field": true}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="q3"):
        load_golden_set(bad)


# AC-1 : missing id field → error
def test_load_missing_id_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        '{"mode": "canon", "question": "x", "expected_contexts": [], "expected_answer_keywords": []}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_golden_set(bad)
