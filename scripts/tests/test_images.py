"""Unit tests for gdrive_export.images — AC-3/3a/3b/3c/AC-5/AC-10/AC-12/AC-18."""

from __future__ import annotations

import hashlib
import io
import math
import random
import re
import tempfile
from pathlib import Path
from typing import Any

import PIL.Image
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gdrive_export.images import (
    ImageOversizedError,
    ImageUndecodableError,
    cleanup_orphans,
    compress_image,
    compute_image_path,
    extract_inline_objects,
    rename_sidecar,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_png(width: int, height: int, mode: str = "RGB") -> bytes:
    """Return raw PNG bytes for an image of the given dimensions."""
    img = PIL.Image.new(mode, (width, height), color=(128, 64, 32))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_noisy_png(width: int, height: int) -> bytes:
    """Return a noisy (high-entropy) PNG, almost incompressible via JPEG."""
    rng = random.Random(42)  # noqa: S311
    pixels = bytes(rng.randint(0, 255) for _ in range(width * height * 3))
    img = PIL.Image.frombytes("RGB", (width, height), pixels)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_large_incompressible_png() -> bytes:
    """Return a PNG whose JPEG re-encode also exceeds 5 MiB.

    Strategy: raw pixel dump stored as PNG with no compression for a 2048x2048
    image, then a JPEG at q=85 of random data stays large enough.
    """
    # Use a repeating high-entropy pattern for a 2048x2048 image.
    # Even at q=85, a full-noise 2048x2048 JPEG is > 5 MiB.
    rng = random.Random(0)  # noqa: S311
    # 2048*2048*3 = 12,582,912 bytes of random RGB
    raw_pixels = bytes(rng.randint(0, 255) for _ in range(2048 * 2048 * 3))
    img = PIL.Image.frombytes("RGB", (2048, 2048), raw_pixels)
    buf = io.BytesIO()
    # Save as uncompressed PNG (compress_level=0) → huge file.
    img.save(buf, format="PNG", compress_level=0)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# extract_inline_objects
# ---------------------------------------------------------------------------


class TestExtractInlineObjects:
    """Unit tests for extract_inline_objects — inlineObjects and positionedObjects."""

    def test_empty_doc(self) -> None:
        assert extract_inline_objects({}) == {}

    def test_single_inline_object(self) -> None:
        doc = {
            "inlineObjects": {
                "kix.abc": {
                    "inlineObjectProperties": {
                        "embeddedObject": {
                            "imageProperties": {
                                "contentUri": "https://lh3.googleusercontent.com/img1"
                            }
                        }
                    }
                }
            }
        }
        result = extract_inline_objects(doc)
        assert result == {"kix.abc": "https://lh3.googleusercontent.com/img1"}

    def test_inline_object_without_uri_is_excluded(self) -> None:
        doc: dict[str, Any] = {
            "inlineObjects": {
                "kix.abc": {
                    "inlineObjectProperties": {
                        "embeddedObject": {}
                    }
                }
            }
        }
        assert extract_inline_objects(doc) == {}

    def test_positioned_object_extracted(self) -> None:
        # Core refactor: positionedObjects are also extracted.
        doc = {
            "positionedObjects": {
                "kix.pos1": {
                    "positionedObjectProperties": {
                        "embeddedObject": {
                            "imageProperties": {
                                "contentUri": "https://lh3.googleusercontent.com/pos1"
                            }
                        }
                    }
                }
            }
        }
        result = extract_inline_objects(doc)
        assert result == {"kix.pos1": "https://lh3.googleusercontent.com/pos1"}

    def test_positioned_object_without_uri_excluded(self) -> None:
        doc: dict[str, Any] = {
            "positionedObjects": {
                "kix.pos1": {
                    "positionedObjectProperties": {
                        "embeddedObject": {}
                    }
                }
            }
        }
        assert extract_inline_objects(doc) == {}

    def test_merged_inline_and_positioned(self) -> None:
        # Both containers contribute to the result.
        doc = {
            "inlineObjects": {
                "kix.inline": {
                    "inlineObjectProperties": {
                        "embeddedObject": {
                            "imageProperties": {
                                "contentUri": "https://lh3.googleusercontent.com/i1"
                            }
                        }
                    }
                }
            },
            "positionedObjects": {
                "kix.pos": {
                    "positionedObjectProperties": {
                        "embeddedObject": {
                            "imageProperties": {
                                "contentUri": "https://lh3.googleusercontent.com/p1"
                            }
                        }
                    }
                }
            },
        }
        result = extract_inline_objects(doc)
        assert result == {
            "kix.inline": "https://lh3.googleusercontent.com/i1",
            "kix.pos": "https://lh3.googleusercontent.com/p1",
        }


# ---------------------------------------------------------------------------
# compress_image — AC-3/3a/3b/3c
# ---------------------------------------------------------------------------


class TestCompressImage:
    """AC-3/3a/3b/3c: two-stage compression pipeline."""

    def test_small_png_noop(self) -> None:
        # AC-3a: 1024x768, < 2048px → no resize, no log, bytes unchanged.
        raw = _make_png(1024, 768)
        result = compress_image(raw, "image/png", "src1", 0)
        assert result.mime_final == "image/png"
        assert result.ext_final == "png"
        assert result.bytes_final == raw
        # No resize log emitted.
        resize_logs = [e for e in result.log_events if e["event"] == "gdrive_sync.image_resized"]
        assert resize_logs == []

    def test_large_png_resize_only(self) -> None:
        # AC-3a: 4096x2048 → resize to 2048x1024, log emitted, no recompress.
        raw = _make_png(4096, 2048)
        result = compress_image(raw, "image/png", "src1", 1)
        assert result.mime_final == "image/png"
        assert result.ext_final == "png"
        resize_logs = [e for e in result.log_events if e["event"] == "gdrive_sync.image_resized"]
        assert len(resize_logs) == 1
        assert resize_logs[0]["width_before"] == 4096
        assert resize_logs[0]["height_before"] == 2048
        assert resize_logs[0]["width_after"] == 2048
        assert resize_logs[0]["height_after"] == 1024

        # Verify output image dimensions.
        out = PIL.Image.open(io.BytesIO(result.bytes_final))
        assert out.size == (2048, 1024)

    def test_very_large_png_resize_then_jpeg(self) -> None:
        # AC-3b: 3000x3000 large PNG → resize to 2048x2048 → if still > 5 MiB: JPEG.
        # We make raw bytes > 5 MiB by constructing a noisy PNG.
        rng = random.Random(1)  # noqa: S311
        raw_pixels = bytes(rng.randint(0, 255) for _ in range(3000 * 3000 * 3))
        img = PIL.Image.frombytes("RGB", (3000, 3000), raw_pixels)
        buf = io.BytesIO()
        img.save(buf, format="PNG", compress_level=0)
        raw = buf.getvalue()
        assert len(raw) > 5 * 1024 * 1024, "test fixture must be > 5 MiB"

        result = compress_image(raw, "image/png", "src1", 2)
        # If JPEG recompression happened, MIME is jpeg.
        recomp_logs = [e for e in result.log_events
                       if e["event"] == "gdrive_sync.image_recompressed"]
        if recomp_logs:
            assert result.mime_final == "image/jpeg"
            assert result.ext_final == "jpg"
            assert recomp_logs[0]["src_mime"] == "image/png"
            assert recomp_logs[0]["dst_mime"] == "image/jpeg"
        # Either way, bytes_final <= 5 MiB (or ImageOversizedError was raised).
        assert len(result.bytes_final) <= 5 * 1024 * 1024

    def test_incompressible_image_raises_oversized(self) -> None:
        # AC-3c: incompressible noise image → ImageOversizedError.
        # Build a 2048x2048 noisy image; save uncompressed PNG, then JPEG q=85 > 5 MiB.
        raw = _make_large_incompressible_png()
        # Only raises if JPEG is still > 5 MiB. If hardware/version compresses below
        # 5 MiB, skip the test (real noise compressibility varies).
        rng = random.Random(0)  # noqa: S311
        buf = io.BytesIO()
        PIL.Image.frombytes("RGB", (2048, 2048),
            bytes(rng.randint(0, 255) for _ in range(2048 * 2048 * 3))
        ).save(buf, format="JPEG", quality=85)
        if len(buf.getvalue()) <= 5 * 1024 * 1024:
            pytest.skip("JPEG encoder compressed below 5 MiB on this platform")

        with pytest.raises(ImageOversizedError) as exc_info:
            compress_image(raw, "image/png", "src1", 3)
        assert exc_info.value.bytes_after_compression > 5 * 1024 * 1024

    def test_unsupported_mime_raises_undecodable(self) -> None:
        # AC-7: image/bmp not in _MIME_TO_EXT → ImageUndecodableError.
        raw = _make_png(100, 100)
        with pytest.raises(ImageUndecodableError, match="unsupported content type"):
            compress_image(raw, "image/bmp", "src1", 0)

    def test_corrupt_payload_raises_undecodable(self) -> None:
        # AC-7: garbage bytes → Pillow UnidentifiedImageError → ImageUndecodableError.
        with pytest.raises(ImageUndecodableError, match="pillow decode failed"):
            compress_image(b"\x00\x00garbage_data", "image/png", "src1", 0)

    def test_decompression_bomb_treated_as_undecodable(self) -> None:
        # Security spec L122: PIL.Image.MAX_IMAGE_PIXELS caps pixel count before resize.
        # A PNG whose declared pixel count exceeds the cap must raise ImageUndecodableError
        # (via DecompressionBombError → caught and reclassified), never silently succeed.
        # Strategy: temporarily lower MAX_IMAGE_PIXELS to 1 so any real image trips the
        # guard, then restore original value.
        original_max = PIL.Image.MAX_IMAGE_PIXELS
        try:
            PIL.Image.MAX_IMAGE_PIXELS = 1
            raw = _make_png(100, 100)  # 10 000 pixels > 1 → triggers DecompressionBombError
            with pytest.raises(ImageUndecodableError):
                compress_image(raw, "image/png", "src1", 0)
        finally:
            PIL.Image.MAX_IMAGE_PIXELS = original_max

    def test_transparent_png_flattened_on_jpeg_encode(self) -> None:
        # AC-3b: RGBA PNG > 5 MiB after resize → JPEG (no alpha), MIME = image/jpeg.
        rng = random.Random(2)  # noqa: S311
        raw_pixels = bytes(rng.randint(0, 255) for _ in range(3000 * 3000 * 4))
        img = PIL.Image.frombytes("RGBA", (3000, 3000), raw_pixels)
        buf = io.BytesIO()
        img.save(buf, format="PNG", compress_level=0)
        raw = buf.getvalue()
        if len(raw) <= 5 * 1024 * 1024:
            pytest.skip("fixture too small for this test")
        result = compress_image(raw, "image/png", "src1", 0)
        recomp = [e for e in result.log_events if e["event"] == "gdrive_sync.image_recompressed"]
        if recomp:
            assert result.mime_final == "image/jpeg"
            out = PIL.Image.open(io.BytesIO(result.bytes_final))
            assert out.mode in ("RGB", "L"), "JPEG output must not have alpha"

    def test_md5_is_post_compression(self) -> None:
        # AC-3/AC-5: MD5 is computed on final compressed bytes.
        raw = _make_png(1024, 768)
        result = compress_image(raw, "image/png", "src1", 0)
        expected_md5 = hashlib.md5(result.bytes_final).hexdigest()[:12]  # noqa: S324
        assert result.md5_hex_12 == expected_md5

    def test_jpeg_source_noop_if_small(self) -> None:
        # Small JPEG → no resize, no recompress.
        img = PIL.Image.new("RGB", (200, 200), color=(10, 20, 30))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        raw = buf.getvalue()
        result = compress_image(raw, "image/jpeg", "src1", 0)
        assert result.mime_final == "image/jpeg"
        assert result.ext_final == "jpg"
        assert result.log_events == []


# ---------------------------------------------------------------------------
# compute_image_path — AC-18
# ---------------------------------------------------------------------------


class TestComputeImagePath:
    """AC-18: path traversal prevention and MD5 regex validation."""

    def test_valid_path(self, tmp_path: Path) -> None:
        lore_root = tmp_path / "lore"
        path = compute_image_path(lore_root, ["subdir"], "my-doc", "abc123def456", "png")
        assert path == lore_root / "subdir" / "my-doc.images" / "abc123def456.png"

    def test_invalid_md5_too_short_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="invalid md5_hex"):
            compute_image_path(tmp_path / "lore", [], "doc", "abc", "png")

    def test_invalid_md5_uppercase_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="invalid md5_hex"):
            compute_image_path(tmp_path / "lore", [], "doc", "ABC123DEF456", "png")

    def test_invalid_md5_non_hex_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="invalid md5_hex"):
            compute_image_path(tmp_path / "lore", [], "doc", "zzzzzzzzzzzz", "png")

    def test_path_is_under_lore_root(self, tmp_path: Path) -> None:
        lore_root = tmp_path / "lore"
        path = compute_image_path(lore_root, [], "doc", "aabbccddeeff", "jpg")
        assert path.resolve().is_relative_to(lore_root.resolve())


# ---------------------------------------------------------------------------
# cleanup_orphans — AC-10
# ---------------------------------------------------------------------------


class TestCleanupOrphans:
    """AC-10: orphan removal and directory cleanup."""

    def test_removes_orphan(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "doc.images"
        sidecar.mkdir()
        (sidecar / "abc123def456.png").write_bytes(b"img1")
        (sidecar / "111222333444.png").write_bytes(b"img2")

        removed = cleanup_orphans(sidecar, {"abc123def456"}, "src1", dry_run=False)
        assert removed == 1
        assert (sidecar / "abc123def456.png").exists()
        assert not (sidecar / "111222333444.png").exists()

    def test_removes_dir_when_empty(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "doc.images"
        sidecar.mkdir()
        (sidecar / "abc123def456.png").write_bytes(b"img")

        removed = cleanup_orphans(sidecar, set(), "src1", dry_run=False)
        assert removed == 1
        assert not sidecar.exists()

    def test_no_op_if_dir_missing(self, tmp_path: Path) -> None:
        removed = cleanup_orphans(tmp_path / "missing.images", set(), "src1", dry_run=False)
        assert removed == 0

    def test_dry_run_does_not_remove(self, tmp_path: Path) -> None:
        sidecar = tmp_path / "doc.images"
        sidecar.mkdir()
        (sidecar / "aabbccddeeff.png").write_bytes(b"img")

        removed = cleanup_orphans(sidecar, set(), "src1", dry_run=True)
        assert removed == 1
        assert (sidecar / "aabbccddeeff.png").exists()  # not deleted in dry-run


# ---------------------------------------------------------------------------
# rename_sidecar — AC-12
# ---------------------------------------------------------------------------


class TestRenameSidecar:
    """AC-12: sidecar directory rename on gdoc rename."""

    def test_renames_existing_sidecar(self, tmp_path: Path) -> None:
        old_md = tmp_path / "old-doc.md"
        new_md = tmp_path / "new-doc.md"
        old_sidecar = tmp_path / "old-doc.images"
        old_sidecar.mkdir()
        (old_sidecar / "abc123def456.png").write_bytes(b"img")

        rename_sidecar(old_md, new_md, "src1")

        new_sidecar = tmp_path / "new-doc.images"
        assert new_sidecar.exists()
        assert (new_sidecar / "abc123def456.png").exists()
        assert not old_sidecar.exists()

    def test_noop_if_sidecar_missing(self, tmp_path: Path) -> None:
        rename_sidecar(tmp_path / "old.md", tmp_path / "new.md", "src1")
        # No error raised.


# ---------------------------------------------------------------------------
# Property tests — AC-3a resize bounds, AC-18 path safety
# ---------------------------------------------------------------------------


@given(
    width=st.integers(min_value=2049, max_value=4095),
    height=st.integers(min_value=2049, max_value=4095),
)
@settings(max_examples=20, deadline=10000)
def test_resize_invariant_max_dim_le_2048(width: int, height: int) -> None:
    """AC-3a property: after compress_image, max(width_after, height_after) <= 2048."""
    # Cap dimensions to avoid exceeding MAX_IMAGE_PIXELS (2048*2048*4 ~ 16.7M).
    # 4095*4095*3 ~ 50M > limit; so cap at values that fit within the limit.
    # Product w*h must stay under MAX_IMAGE_PIXELS / bytes_per_pixel.
    max_px = 2048 * 2048 * 4
    if width * height * 3 > max_px:
        # Scale down proportionally.
        scale = math.sqrt(max_px / (width * height * 3))
        width = max(1, int(width * scale))
        height = max(1, int(height * scale))
    if max(width, height) <= 2048:
        return  # no resize triggered — skip
    img = PIL.Image.new("RGB", (width, height), color=(100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    result = compress_image(buf.getvalue(), "image/png", "src-prop", 0)
    out = PIL.Image.open(io.BytesIO(result.bytes_final))
    assert max(out.size) <= 2048


@given(
    width=st.integers(min_value=2049, max_value=4096),
    height=st.integers(min_value=2049, max_value=4096),
)
@settings(max_examples=20, deadline=10000)
def test_resize_ratio_preserved(width: int, height: int) -> None:
    """AC-3a property: aspect ratio preserved to ±1px after resize."""
    img = PIL.Image.new("RGB", (width, height), color=(50, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    result = compress_image(buf.getvalue(), "image/png", "src-prop", 0)
    out = PIL.Image.open(io.BytesIO(result.bytes_final))
    w_out, h_out = out.size
    original_ratio = width / height
    output_ratio = w_out / h_out
    assert abs(original_ratio - output_ratio) < 0.01, (
        f"ratio mismatch: {original_ratio:.4f} vs {output_ratio:.4f} "
        f"(in={width}x{height}, out={w_out}x{h_out})"
    )


@given(
    md5_hex=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
        min_size=12, max_size=12,
    )
)
@settings(max_examples=50)
def test_invalid_md5_rejected(md5_hex: str) -> None:
    """AC-18 property: non-lowercase-hex MD5s raise ValueError."""
    valid = re.compile(r"^[0-9a-f]{12}$")
    with tempfile.TemporaryDirectory() as td:
        lore_root = Path(td) / "lore"
        if valid.match(md5_hex):
            path = compute_image_path(lore_root, [], "doc", md5_hex, "png")
            assert path.name == f"{md5_hex}.png"
        else:
            with pytest.raises(ValueError, match="invalid md5_hex"):
                compute_image_path(lore_root, [], "doc", md5_hex, "png")
