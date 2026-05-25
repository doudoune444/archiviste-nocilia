"""Unit tests for services/acl.py — permission matrix + fail-closed (AC-3, AC-20, D-4)."""

from __future__ import annotations

import ast
import inspect

import pytest

from archiviste_workers.generate.models import Chunk
from archiviste_workers.services.acl import FilterResult, filter_chunks_by_tier

# AC-3 fixed mapping (verbatim from spec):
#   anonymous  → {"public"}
#   members    → {"public", "members"}
#   author_only → {"public", "members", "author_only"}

_MATRIX: list[tuple[str, str, bool]] = [
    # (user_tier, chunk_access_tier, expected_visible)
    ("anonymous", "public", True),
    ("anonymous", "members", False),
    ("anonymous", "author_only", False),
    ("members", "public", True),
    ("members", "members", True),
    ("members", "author_only", False),
    ("author_only", "public", True),
    ("author_only", "members", True),
    ("author_only", "author_only", True),
]


def _make_chunk(access_tier: str, idx: int = 0) -> Chunk:
    return Chunk(
        source_path=f"doc-{idx}.md",
        ord=idx,
        text=f"text-{idx}",
        score=0.8,
        access_tier=access_tier,
    )


@pytest.mark.parametrize(("user_tier", "access_tier", "expected_visible"), _MATRIX)
def test_permission_matrix(user_tier: str, access_tier: str, expected_visible: bool) -> None:
    # AC-3: 9-cell permission matrix.
    chunk = _make_chunk(access_tier)
    result = filter_chunks_by_tier([chunk], user_tier)
    assert isinstance(result, FilterResult)
    if expected_visible:
        assert len(result.visible) == 1
        assert result.blocked_count == 0
    else:
        assert len(result.visible) == 0
        assert result.blocked_count == 1


def test_filter_empty_chunks() -> None:
    # AC-3: empty list → FilterResult(visible=[], blocked_count=0).
    result = filter_chunks_by_tier([], "anonymous")
    assert result.visible == []
    assert result.blocked_count == 0


def test_partial_block() -> None:
    # AC-19: 3 public + 2 members → anonymous → 3 visible, blocked_count=2.
    chunks = [_make_chunk("public", i) for i in range(3)] + [
        _make_chunk("members", i + 3) for i in range(2)
    ]
    result = filter_chunks_by_tier(chunks, "anonymous")
    assert len(result.visible) == 3
    assert result.blocked_count == 2


def test_all_blocked() -> None:
    # AC-4: all author_only + user=members → visible=[], blocked_count=5.
    chunks = [_make_chunk("author_only", i) for i in range(5)]
    result = filter_chunks_by_tier(chunks, "members")
    assert result.visible == []
    assert result.blocked_count == 5


def test_fail_closed_unknown_chunk_tier(caplog: pytest.LogCaptureFixture) -> None:
    # D-4: unknown tier → chunk treated as blocked, blocked_count incremented.
    chunk = _make_chunk("ultra_secret")
    result = filter_chunks_by_tier([chunk], "author_only")
    assert result.visible == []
    assert result.blocked_count == 1


def test_fail_closed_unknown_user_tier() -> None:
    # D-4: unknown user_tier → no allowed tiers → all blocked.
    chunks = [_make_chunk("public", 0), _make_chunk("members", 1)]
    result = filter_chunks_by_tier(chunks, "superadmin")
    assert result.visible == []
    assert result.blocked_count == 2


def test_filter_result_fields() -> None:
    # AC-3: FilterResult carries exactly (visible, blocked_count).
    result = FilterResult(visible=[], blocked_count=0)
    assert hasattr(result, "visible")
    assert hasattr(result, "blocked_count")


def test_no_forbidden_imports() -> None:
    # AC-20: acl.py must not import fastapi, httpx, or asyncpg.
    import archiviste_workers.services.acl as acl_mod  # noqa: PLC0415

    source = inspect.getsource(acl_mod)
    tree = ast.parse(source)
    forbidden = {"fastapi", "httpx", "asyncpg"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in forbidden, f"Forbidden import found: {alias.name}"
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".")[0]
            assert root not in forbidden, f"Forbidden from-import found: {module}"
