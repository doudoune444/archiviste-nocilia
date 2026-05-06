"""Unit tests for `archiviste_workers.ingest.frontmatter` (AC-2, AC-3)."""

from __future__ import annotations

import pytest

from archiviste_workers.ingest.frontmatter import (
    Frontmatter,
    FrontmatterError,
    parse_frontmatter,
)


def test_parses_minimal_valid_frontmatter() -> None:
    # AC-2: title required, tags default [], access_tier default "public".
    raw = "---\ntitle: Hello\n---\nbody text\n"
    fm, body = parse_frontmatter(raw)
    assert fm == Frontmatter(title="Hello", tags=[], access_tier="public")
    assert body == "body text\n"


def test_parses_explicit_tags_and_access_tier() -> None:
    # AC-2: explicit values respected.
    raw = "---\ntitle: T\ntags: [a, b]\naccess_tier: members\n---\nbody"
    fm, body = parse_frontmatter(raw)
    assert fm.tags == ["a", "b"]
    assert fm.access_tier == "members"
    assert body == "body"


def test_parses_author_only_tier() -> None:
    raw = "---\ntitle: T\naccess_tier: author_only\n---\n"
    fm, _ = parse_frontmatter(raw)
    assert fm.access_tier == "author_only"


def test_skips_when_no_frontmatter() -> None:
    # AC-3: missing FM → FrontmatterError.
    with pytest.raises(FrontmatterError):
        parse_frontmatter("just body, no delimiters\n")


def test_skips_when_title_missing() -> None:
    # AC-3
    with pytest.raises(FrontmatterError, match="title"):
        parse_frontmatter("---\ntags: []\n---\nbody")


def test_skips_when_title_empty() -> None:
    # AC-3
    with pytest.raises(FrontmatterError, match="title"):
        parse_frontmatter('---\ntitle: "   "\n---\nbody')


def test_skips_when_access_tier_invalid() -> None:
    # AC-3
    with pytest.raises(FrontmatterError, match="access_tier"):
        parse_frontmatter("---\ntitle: T\naccess_tier: god\n---\nbody")


def test_skips_when_tags_not_list_of_strings() -> None:
    # AC-3 (defensive; tags type strict).
    with pytest.raises(FrontmatterError, match="tags"):
        parse_frontmatter("---\ntitle: T\ntags: [1, 2]\n---\nbody")
