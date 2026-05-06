"""Unit tests for `archiviste_workers.ingest.normalize` (AC-4, AC-5)."""

from __future__ import annotations

import hashlib
import unicodedata

from archiviste_workers.ingest.normalize import compute_content_hash, normalize_body


def test_nfkc_strip_control_preserves_newline_and_tab() -> None:
    # AC-4: NFKC + strip C0/DEL except \n and \t.
    raw = "ﬁ\n\tA\x00B\x01C\x7fD"
    expected = unicodedata.normalize("NFKC", "fi\n\tABCD")
    assert normalize_body(raw) == expected


def test_normalize_keeps_visible_unicode() -> None:
    # AC-4: non-ASCII chars kept (NFKC may transform but not drop).
    raw = "café — naïve"
    out = normalize_body(raw)
    assert "café" in out
    assert "naïve" in out


def test_content_hash_is_sha256_hex_of_normalized_utf8() -> None:
    # AC-5: hash deterministic + matches manual sha256.
    body = "hello world"
    expected = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert compute_content_hash(body) == expected
    assert len(compute_content_hash(body)) == 64


def test_content_hash_differs_for_different_content() -> None:
    assert compute_content_hash("a") != compute_content_hash("b")
