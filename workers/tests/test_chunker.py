"""Unit tests for `archiviste_workers.ingest.chunker` — ING-016.

AC-1: chunker.py does not import transformers.
AC-2: tokenizer is Mixtral-8x7B-v0.1 via `tokenizers` lib, no new runtime dep.
AC-3: init is network-free (loads from vendored asset).
AC-4: chunks respect chunk_size=512 / chunk_overlap=64 in Mixtral tokens.
AC-5: pyproject.toml has no `transformers` dependency.
AC-6: pyproject.toml has no `embedder-fallback` block / `sentence-transformers`.
"""

from __future__ import annotations

import inspect
import socket
import sys
import tomllib
from pathlib import Path

import pytest
from tokenizers import Tokenizer  # type: ignore[import-untyped]

import archiviste_workers.ingest.chunker as _chunker_mod
from archiviste_workers.ingest.chunker import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    SEPARATORS,
    build_chunker,
)

PYPROJECT_PATH = Path(__file__).parents[1] / "pyproject.toml"
_ASSET_PATH = (
    Path(inspect.getfile(_chunker_mod)).parent / "assets" / "mixtral_8x7b_v0_1_tokenizer.json"
)
FIXTURE_DOC = (
    "Nocilia est une cité engloutie dans les méandres du Voile.\n\n"
    + "Les Gardiens du Seuil veillent depuis des siècles sur les Archives. " * 40
    + "\n\n"
    + "L'Archiviste consigne chaque trace, chaque écho, chaque fragment de vérité. " * 40
)


# ── AC-1 ─────────────────────────────────────────────────────────────────────


def test_chunker_source_has_no_transformers_import() -> None:
    # AC-1: assert no `import transformers` / `from transformers` in chunker source.
    source = inspect.getsource(_chunker_mod)
    assert "import transformers" not in source
    assert "from transformers" not in source


def test_transformers_not_in_sys_modules_after_chunker_import() -> None:
    # AC-1: transformers must never be imported by the chunker module.
    assert "transformers" not in sys.modules


# ── AC-2: identity pin ────────────────────────────────────────────────────────


def test_tokenizer_identity_vocab_size() -> None:
    # AC-2: vendored tokenizer is Mixtral-8x7B-v0.1 — vocab_size must be 32000.
    tok = Tokenizer.from_file(str(_ASSET_PATH))
    assert tok.get_vocab_size() == 32000


def test_tokenizer_identity_eos_token_id() -> None:
    # AC-2: EOS token `</s>` must encode to include id 2 (Mistral SP-BPE pin).
    tok = Tokenizer.from_file(str(_ASSET_PATH))
    encoded = tok.encode("</s>")
    assert 2 in encoded.ids


def test_no_new_runtime_dep_in_pyproject() -> None:
    # AC-2: `tokenizers` lib is already transitive — not added to [project.dependencies].
    with PYPROJECT_PATH.open("rb") as fh:
        data = tomllib.load(fh)
    deps: list[str] = data["project"]["dependencies"]
    dep_names = {d.split(">=")[0].split("==")[0].strip().lower() for d in deps}
    # `tokenizers` must NOT appear as a direct dep (it is already transitive).
    assert "tokenizers" not in dep_names


# ── AC-3 ─────────────────────────────────────────────────────────────────────


def test_build_chunker_is_network_free(monkeypatch: pytest.MonkeyPatch) -> None:
    # AC-3: init must not open any network socket.
    def _block_socket(*args: object, **kwargs: object) -> None:
        raise OSError("network access forbidden in chunker init (AC-3)")

    monkeypatch.setattr(socket, "socket", _block_socket)
    build_chunker()  # must succeed with network blocked


def test_build_chunker_calls_from_file_not_from_pretrained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # AC-3: Tokenizer.from_pretrained must never be called; from_file must be called.
    calls: list[str] = []
    original_from_file = Tokenizer.from_file

    def _spy_from_file(path: str) -> object:
        calls.append(f"from_file:{path}")
        return original_from_file(path)

    def _fail_from_pretrained(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tokenizer.from_pretrained called — forbidden (AC-3)")

    monkeypatch.setattr(Tokenizer, "from_file", staticmethod(_spy_from_file))
    monkeypatch.setattr(Tokenizer, "from_pretrained", staticmethod(_fail_from_pretrained))

    build_chunker()

    assert any("from_file:" in c for c in calls), "from_file was never called"


# ── AC-4 ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def chunker() -> object:
    return build_chunker()


@pytest.fixture(scope="module")
def mixtral_tokenizer() -> Tokenizer:
    return Tokenizer.from_file(str(_ASSET_PATH))


def test_chunker_constants_match_spec() -> None:
    # AC-4: chunk_size=512, chunk_overlap=64, separators ordered.
    assert CHUNK_SIZE == 512
    assert CHUNK_OVERLAP == 64
    assert SEPARATORS == ["\n\n", "\n", ". ", " ", ""]


def test_each_chunk_within_token_limit(chunker: object, mixtral_tokenizer: Tokenizer) -> None:
    # AC-4: every chunk must be ≤ chunk_size=512 Mixtral tokens.
    chunks: list[str] = chunker.split_text(FIXTURE_DOC)  # type: ignore[attr-defined]
    assert len(chunks) >= 2, "fixture doc must produce at least 2 chunks"
    for chunk in chunks:
        token_count = len(mixtral_tokenizer.encode(chunk).ids)
        assert token_count <= CHUNK_SIZE, (
            f"chunk exceeds {CHUNK_SIZE} tokens ({token_count}): {chunk[:80]!r}"
        )


def test_chunks_are_non_empty_and_ordered(chunker: object) -> None:
    # AC-4: all chunks are non-empty strings; order preserved.
    chunks: list[str] = chunker.split_text(FIXTURE_DOC)  # type: ignore[attr-defined]
    assert len(chunks) >= 2
    for chunk in chunks:
        assert isinstance(chunk, str)
        assert chunk.strip()


def test_chunker_short_text_single_chunk(chunker: object) -> None:
    # AC-4: text shorter than chunk_size yields exactly one chunk.
    chunks: list[str] = chunker.split_text("Bonjour")  # type: ignore[attr-defined]
    assert chunks == ["Bonjour"]


def test_adjacent_chunks_share_overlap(chunker: object, mixtral_tokenizer: Tokenizer) -> None:
    # AC-4: adjacent chunks must overlap (overlap > 0 expected when splitting occurs).
    long_text = "Les Gardiens du Seuil veillent sur les Archives. " * 300
    chunks: list[str] = chunker.split_text(long_text)  # type: ignore[attr-defined]
    if len(chunks) < 2:
        pytest.skip("not enough chunks to test overlap")
    found_overlap = False
    for i in range(len(chunks) - 1):
        a, b = chunks[i], chunks[i + 1]
        for length in range(1, min(len(a), len(b)) + 1):
            if a[-length:] == b[:length]:
                found_overlap = True
                break
        if found_overlap:
            break
    assert found_overlap, "no overlap found between any adjacent chunk pair"


# ── AC-5 ─────────────────────────────────────────────────────────────────────


def test_transformers_absent_from_all_dep_tables() -> None:
    # AC-5: `transformers` must not appear in any dependency table.
    with PYPROJECT_PATH.open("rb") as fh:
        data = tomllib.load(fh)

    def _has_transformers(deps: list[str]) -> bool:
        return any(d.strip().lower().startswith("transformers") for d in deps)

    assert not _has_transformers(data["project"].get("dependencies", []))
    for _name, group in data.get("project", {}).get("optional-dependencies", {}).items():
        assert not _has_transformers(group)


# ── AC-6 ─────────────────────────────────────────────────────────────────────


def test_embedder_fallback_block_absent() -> None:
    # AC-6: `embedder-fallback` optional-dep group must not exist.
    with PYPROJECT_PATH.open("rb") as fh:
        data = tomllib.load(fh)
    optional: dict[str, list[str]] = data.get("project", {}).get("optional-dependencies", {})
    assert "embedder-fallback" not in optional


def test_sentence_transformers_absent_from_all_tables() -> None:
    # AC-6: `sentence-transformers` must not appear anywhere.
    with PYPROJECT_PATH.open("rb") as fh:
        data = tomllib.load(fh)

    def _has_st(deps: list[str]) -> bool:
        return any(d.strip().lower().startswith("sentence-transformers") for d in deps)

    assert not _has_st(data["project"].get("dependencies", []))
    for _name, group in data.get("project", {}).get("optional-dependencies", {}).items():
        assert not _has_st(group)
