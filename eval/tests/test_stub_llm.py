"""Tests for eval/stub_llm.py — AC-4."""

from __future__ import annotations

import hashlib

from eval.stub_llm import RetrievedChunk, build_stub_answer


# AC-4 : rule is fixed — keywords + double newline + chunk text[:200]
def test_build_stub_answer_rule() -> None:
    chunks = [RetrievedChunk(source_path="intro_p01", text="Hello world")]
    result = build_stub_answer(["archiviste", "nocilia"], chunks)
    assert result == "archiviste nocilia\n\nHello world"


# AC-4 : chunks truncated to 200 chars
def test_build_stub_answer_truncates_chunk_text() -> None:
    long_text = "x" * 300
    chunks = [RetrievedChunk(source_path="p01", text=long_text)]
    result = build_stub_answer(["kw"], chunks)
    assert result == "kw\n\n" + "x" * 200


# AC-4 : multiple chunks joined by double newline
def test_build_stub_answer_multiple_chunks() -> None:
    chunks = [
        RetrievedChunk(source_path="p01", text="First chunk"),
        RetrievedChunk(source_path="p02", text="Second chunk"),
    ]
    result = build_stub_answer(["keyword"], chunks)
    assert result == "keyword\n\nFirst chunk\n\nSecond chunk"


# AC-4 : empty keywords produces empty prefix
def test_build_stub_answer_empty_keywords() -> None:
    chunks = [RetrievedChunk(source_path="p01", text="text")]
    result = build_stub_answer([], chunks)
    assert result == "\n\ntext"


# AC-4 : two consecutive runs produce byte-identical output (determinism)
def test_build_stub_answer_deterministic_double_run() -> None:
    keywords = ["archiviste", "nocilia", "lore"]
    chunks = [
        RetrievedChunk(source_path="intro_p01", text="Introduction to Nocilia " * 20),
        RetrievedChunk(source_path="meta_p02", text="Meta information " * 15),
    ]
    result_1 = build_stub_answer(keywords, chunks)
    result_2 = build_stub_answer(keywords, chunks)
    sha1 = hashlib.sha256(result_1.encode()).hexdigest()
    sha2 = hashlib.sha256(result_2.encode()).hexdigest()
    assert sha1 == sha2
