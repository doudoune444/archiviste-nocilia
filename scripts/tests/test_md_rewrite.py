"""Unit tests for gdrive_export.md_rewrite — AC-4/6."""

from __future__ import annotations

import io
import json
import re
import sys

from gdrive_export.md_rewrite import ImageResolution, rewrite_image_refs


def _capture_logs(
    body_md: str,
    ordered_ids: list[str],
    resolutions: dict[str, ImageResolution],
    source_id: str,
) -> tuple[str, list[dict[str, object]]]:
    """Run rewrite_image_refs and capture stdout JSON logs."""
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        result = rewrite_image_refs(body_md, ordered_ids, resolutions, source_id)
    finally:
        sys.stdout = old_stdout
    logs = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    return result, logs


def _ok(obj_id: str, rel_path: str) -> ImageResolution:
    return ImageResolution(kind="ok", object_id=obj_id, rel_path=rel_path, alt=None)


def _failed(obj_id: str) -> ImageResolution:
    return ImageResolution(kind="failed", object_id=obj_id, rel_path=None, alt=None)


def _oversized(obj_id: str) -> ImageResolution:
    return ImageResolution(kind="oversized", object_id=obj_id, rel_path=None, alt=None)


# ---------------------------------------------------------------------------
# AC-4: image reference rewriting
# ---------------------------------------------------------------------------


class TestRewriteImageRefs:
    """AC-4: correct rewriting of inline image references."""

    def test_single_image_rewritten(self) -> None:
        body = "# Title\n\n![alt text](https://lh3.googleusercontent.com/img1)\n"
        ids = ["kix.a"]
        resolutions = {"kix.a": _ok("kix.a", "my-doc.images/abc123def456.png")}
        result, _ = _capture_logs(body, ids, resolutions, "src1")
        assert "my-doc.images/abc123def456.png" in result
        assert "lh3.googleusercontent.com" not in result

    def test_multiple_images_positional_order(self) -> None:
        # AC-4/OQ-2: n-th image → n-th objectId.
        body = (
            "![](https://lh3.googleusercontent.com/img1)\n"
            "Some text.\n"
            "![](https://lh3.googleusercontent.com/img2)\n"
        )
        ids = ["kix.a", "kix.b"]
        resolutions = {
            "kix.a": _ok("kix.a", "doc.images/aaaaaaaaaaaa.png"),
            "kix.b": _ok("kix.b", "doc.images/bbbbbbbbbbbb.jpg"),
        }
        result, _ = _capture_logs(body, ids, resolutions, "src1")
        assert "doc.images/aaaaaaaaaaaa.png" in result
        assert "doc.images/bbbbbbbbbbbb.jpg" in result

    def test_alt_text_preserved(self) -> None:
        body = "![my alt](https://lh3.googleusercontent.com/img)\n"
        ids = ["kix.a"]
        resolutions = {"kix.a": _ok("kix.a", "doc.images/abc123def456.png")}
        result, _ = _capture_logs(body, ids, resolutions, "src1")
        assert "![my alt](doc.images/abc123def456.png)" in result

    def test_failed_image_gets_placeholder(self) -> None:
        # AC-7: failed → placeholder #image-failed-<objectId>.
        body = "![](https://lh3.googleusercontent.com/img)\n"
        ids = ["kix.abc"]
        resolutions = {"kix.abc": _failed("kix.abc")}
        result, _ = _capture_logs(body, ids, resolutions, "src1")
        assert "#image-failed-kix.abc" in result

    def test_oversized_image_gets_placeholder(self) -> None:
        # AC-3c: oversized → placeholder #oversized-<objectId>.
        body = "![](https://lh3.googleusercontent.com/img)\n"
        ids = ["kix.abc"]
        resolutions = {"kix.abc": _oversized("kix.abc")}
        result, _ = _capture_logs(body, ids, resolutions, "src1")
        assert "#oversized-kix.abc" in result

    def test_path_no_leading_slash_or_dotslash(self) -> None:
        # AC-4: path must be relative, no / or ./ prefix.
        body = "![](https://lh3.googleusercontent.com/img)\n"
        ids = ["kix.a"]
        resolutions = {"kix.a": _ok("kix.a", "my-doc.images/abc123def456.png")}
        result, _ = _capture_logs(body, ids, resolutions, "src1")
        # Should not start with / or ./
        match = re.search(r"\(([^)]+)\)", result)
        assert match is not None
        href = match.group(1)
        assert not href.startswith("/")
        assert not href.startswith("./")

    def test_no_images_in_doc_passthrough(self) -> None:
        # AC-13: no images → body unchanged.
        body = "# Title\n\nJust text here.\n"
        result, _ = _capture_logs(body, [], {}, "src1")
        assert result == body

    def test_image_count_overflow_passthrough(self) -> None:
        # More images in Markdown than objectIds → overflow refs pass through.
        body = (
            "![a](https://lh3.googleusercontent.com/img1)\n"
            "![b](https://lh3.googleusercontent.com/img2)\n"
        )
        ids = ["kix.a"]
        resolutions = {"kix.a": _ok("kix.a", "doc.images/abc123def456.png")}
        result, _ = _capture_logs(body, ids, resolutions, "src1")
        # First image rewritten, second passes through.
        assert "doc.images/abc123def456.png" in result
        assert "lh3.googleusercontent.com/img2" in result

    def test_dedup_two_refs_same_objectid_same_path(self) -> None:
        # AC-5: two images with same resolution point to same path.
        body = (
            "![](https://lh3.googleusercontent.com/img1)\n"
            "![](https://lh3.googleusercontent.com/img2)\n"
        )
        ids = ["kix.a", "kix.b"]
        shared_path = "doc.images/aabbccddeeff.png"
        resolutions = {
            "kix.a": _ok("kix.a", shared_path),
            "kix.b": _ok("kix.b", shared_path),
        }
        result, _ = _capture_logs(body, ids, resolutions, "src1")
        assert result.count(shared_path) == 2


# ---------------------------------------------------------------------------
# AC-6: Markdown subset — unsupported constructs pass through with warning
# ---------------------------------------------------------------------------


class TestMarkdownSubsetFallback:
    """AC-6: unsupported constructs conserved verbatim, warning logged once."""

    def test_table_passthrough_with_log(self) -> None:
        body = "| col1 | col2 |\n|------|------|\n| a    | b    |\n"
        result, logs = _capture_logs(body, [], {}, "src1")
        assert result == body
        fallback_logs = [lg for lg in logs if lg.get("event") == "gdrive_sync.md_subset_fallback"]
        assert any(lg.get("construct") == "table" for lg in fallback_logs)

    def test_table_warning_emitted_once_per_source_id(self) -> None:
        # Warning emitted only once per construct per source_id per call.
        body = (
            "| a | b |\n|---|---|\n| 1 | 2 |\n"
            "| c | d |\n|---|---|\n| 3 | 4 |\n"
        )
        _, logs = _capture_logs(body, [], {}, "src1")
        fallback = [lg for lg in logs if lg.get("construct") == "table"]
        assert len(fallback) == 1

    def test_paragraph_rewritten_correctly(self) -> None:
        body = "Just a paragraph.\n\nAnother paragraph.\n"
        result, _ = _capture_logs(body, [], {}, "src1")
        assert result == body

    def test_heading_passthrough(self) -> None:
        for level in range(1, 7):
            body = f"{'#' * level} Title\n\nContent.\n"
            result, _ = _capture_logs(body, [], {}, "src1")
            assert result == body

    def test_bold_italic_link_passthrough(self) -> None:
        body = "**bold** *italic* [link](https://example.com)\n"
        result, _ = _capture_logs(body, [], {}, "src1")
        assert result == body

    def test_unordered_list_passthrough(self) -> None:
        body = "- item 1\n- item 2\n- item 3\n"
        result, _ = _capture_logs(body, [], {}, "src1")
        assert result == body

    def test_ordered_list_passthrough(self) -> None:
        body = "1. first\n2. second\n3. third\n"
        result, _ = _capture_logs(body, [], {}, "src1")
        assert result == body

    def test_fenced_code_passthrough_with_log(self) -> None:
        body = "```python\nprint('hello')\n```\n"
        result, logs = _capture_logs(body, [], {}, "src1")
        assert result == body
        fallback = [lg for lg in logs if lg.get("construct") == "fenced_code"]
        assert len(fallback) == 1
