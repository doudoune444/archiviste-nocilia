"""Unit + property tests for gdrive_export.slugify — AC-2, AC-3."""

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gdrive_export.slugify import MAX_LEN, slugify

# Acceptance criteria: AC-2 pipeline, AC-3 determinism + idempotence + alphabet.
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")
_FALLBACK_PATTERN = re.compile(r"^file-[0-9a-f]{8}$")


class TestSluggifyMatrix:
    """AC-2 matrix: Unicode, CJK, emoji, spaces, long strings, edge cases."""

    def test_simple_ascii(self) -> None:
        assert slugify("Hello World", "abc12345") == "hello-world"

    def test_latin_diacritics(self) -> None:
        # NFKD drops combining marks: é→e, ü→u, ñ→n
        assert slugify("Héros de l'été", "abc12345") == "heros-de-l-ete"

    def test_cjk_becomes_fallback(self) -> None:
        # CJK characters are non-alnum after NFKD, leaving nothing
        result = slugify("中文文件", "abcdef12")
        assert result == "file-abcdef12"

    def test_emoji_dropped(self) -> None:
        # Emojis are non-ASCII, dropped by NFKD→replace pipeline
        result = slugify("My 🎉 File", "abc12345")
        assert result == "my-file"

    def test_multiple_spaces_collapsed(self) -> None:
        assert slugify("a   b   c", "abc12345") == "a-b-c"

    def test_leading_trailing_hyphens_stripped(self) -> None:
        assert slugify("---hello---", "abc12345") == "hello"

    def test_already_a_slug(self) -> None:
        assert slugify("already-a-slug", "abc12345") == "already-a-slug"

    def test_empty_string_fallback(self) -> None:
        result = slugify("", "abcdef12")
        assert result == "file-abcdef12"

    def test_only_punctuation_fallback(self) -> None:
        result = slugify("!!!---###", "abcdef12")
        assert result == "file-abcdef12"

    def test_length_cap_at_80(self) -> None:
        long_text = "a" * 100
        result = slugify(long_text, "abc12345")
        assert len(result) <= MAX_LEN

    def test_length_exactly_80(self) -> None:
        text = "a" * 80
        assert slugify(text, "abc12345") == "a" * 80

    def test_length_81_capped(self) -> None:
        text = "a" * 81
        assert len(slugify(text, "abc12345")) == MAX_LEN

    def test_numbers_preserved(self) -> None:
        assert slugify("Chapter 42: Final", "abc12345") == "chapter-42-final"

    def test_uppercase_lowered(self) -> None:
        assert slugify("ALL CAPS", "abc12345") == "all-caps"

    def test_fallback_uses_first_8_chars(self) -> None:
        result = slugify("", "abcdef1234567890")
        assert result == "file-abcdef12"

    def test_mixed_unicode_and_ascii(self) -> None:
        result = slugify("Fichier Été 2024", "abc12345")
        assert result == "fichier-ete-2024"


class TestSlugifyIdempotence:
    """AC-3: idempotence — slugify(slugify(s)) == slugify(s)."""

    @pytest.mark.parametrize(
        "text",
        [
            "Hello World",
            "Héros de l'été",
            "My 🎉 File",
            "already-a-slug",
            "a" * 100,
            "",
            "!!!",
            "中文",
        ],
    )
    def test_idempotent(self, text: str) -> None:
        fallback_id = "abcdef12"
        first = slugify(text, fallback_id)
        second = slugify(first, fallback_id)
        assert first == second, f"Not idempotent for {text!r}: {first!r} → {second!r}"


@given(text=st.text(), fallback_id=st.from_regex(r"[0-9a-f]{8}", fullmatch=True))
@settings(max_examples=200)
def test_property_idempotence(text: str, fallback_id: str) -> None:
    """AC-3 property: slugify(slugify(s, fid), fid) == slugify(s, fid)."""
    first = slugify(text, fallback_id)
    second = slugify(first, fallback_id)
    assert first == second


@given(text=st.text(), fallback_id=st.from_regex(r"[0-9a-f]{8}", fullmatch=True))
@settings(max_examples=200)
def test_property_length(text: str, fallback_id: str) -> None:
    """AC-3 property: len(slug) <= 80."""
    result = slugify(text, fallback_id)
    assert len(result) <= MAX_LEN


@given(text=st.text(), fallback_id=st.from_regex(r"[0-9a-f]{8}", fullmatch=True))
@settings(max_examples=200)
def test_property_alphabet(text: str, fallback_id: str) -> None:
    """AC-3 property: output matches ^[a-z0-9-]+$ or ^file-[0-9a-f]{8}$."""
    result = slugify(text, fallback_id)
    is_slug = bool(re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$", result))
    is_fallback = bool(_FALLBACK_PATTERN.match(result))
    assert is_slug or is_fallback, f"Result {result!r} matches neither pattern"
