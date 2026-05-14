"""Tests for eval/seed_test_corpus.py — non-zero embeddings and lore-dummy texts."""

from __future__ import annotations

import json
from pathlib import Path

from eval.seed_test_corpus import _collect_seed_data, _hash_embedding, _lore_text

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "ci_smoke_qa.jsonl"


def test_hash_embedding_non_zero() -> None:
    """Embedding must not be all zeros (HIGH-B fix: no ZERO_EMBEDDING)."""
    vec_str = _hash_embedding("intro_p01")
    floats = [float(v) for v in vec_str.strip("[]").split(",")]
    assert len(floats) == 1024
    assert any(v != 0.0 for v in floats), "embedding must not be all zeros"


def test_hash_embedding_differentiated() -> None:
    """Two distinct source_paths must produce different embeddings."""
    vec_a = _hash_embedding("intro_p01")
    vec_b = _hash_embedding("meta_p02")
    assert vec_a != vec_b, "embeddings for different source_paths must differ"


def test_hash_embedding_deterministic() -> None:
    """Same source_path must always produce the same embedding."""
    assert _hash_embedding("lore_p01") == _hash_embedding("lore_p01")


def test_hash_embedding_normalised() -> None:
    """Embedding must be L2-normalised (norm ≈ 1.0)."""
    vec_str = _hash_embedding("gap_p01")
    floats = [float(v) for v in vec_str.strip("[]").split(",")]
    norm_sq = sum(v * v for v in floats)
    assert abs(norm_sq - 1.0) < 1e-3, f"embedding norm² = {norm_sq}, expected ≈ 1.0"


def test_lore_text_contains_keywords() -> None:
    """Lore-dummy text must contain each keyword (for keyword_overlap_rate signal)."""
    text = _lore_text("intro_p01", ["archiviste", "nocilia"])
    assert "archiviste" in text.lower()
    assert "nocilia" in text.lower()


def test_lore_text_not_bare_join() -> None:
    """Lore-dummy text must not be a bare keyword join."""
    keywords = ["archiviste", "nocilia"]
    bare = " ".join(keywords)
    text = _lore_text("intro_p01", keywords)
    assert text != bare, "lore text must differ from bare keyword join"
    # Must be longer — embedded in narrative.
    assert len(text) > len(bare) + 20, "lore text must be a narrative, not just keywords"


def test_collect_seed_data_returns_lore_texts() -> None:
    """_collect_seed_data must return entries with narrative chunk texts."""
    seed = _collect_seed_data(FIXTURE_PATH)
    assert len(seed) > 0, "seed data must not be empty"
    for source_path, (chunk_text, embedding) in seed.items():
        # Text must not be a bare source_path or bare keyword join
        assert len(chunk_text) > len(source_path), (
            f"{source_path}: chunk_text too short (bare source_path?)"
        )
        # Embedding must not be all zeros
        floats = [float(v) for v in embedding.strip("[]").split(",")]
        assert any(v != 0.0 for v in floats), f"{source_path}: embedding is all zeros"


def test_collect_seed_data_idempotent() -> None:
    """Two calls to _collect_seed_data on the same fixture must return identical results."""
    seed_a = _collect_seed_data(FIXTURE_PATH)
    seed_b = _collect_seed_data(FIXTURE_PATH)
    assert seed_a == seed_b, "_collect_seed_data must be deterministic"


def test_ci_smoke_fixture_is_valid_jsonl() -> None:
    """ci_smoke_qa.jsonl must be valid JSONL with required fields."""
    with FIXTURE_PATH.open(encoding="utf-8") as fh:
        entries = [json.loads(line) for line in fh if line.strip()]
    assert len(entries) >= 8, "CI smoke fixture must have at least 8 entries"
    for entry in entries:
        assert "id" in entry
        assert "mode" in entry
        assert "question" in entry
        assert entry["mode"] in {"canon", "off_topic", "lore_gap", "mystery"}
