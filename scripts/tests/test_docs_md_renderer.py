"""Unit tests for gdrive_export.docs_md_renderer.

AC-1/AC-4/AC-6/AC-13: renderer walks Docs API body.content + positionedObjects
and returns markdown text with final sidecar image refs embedded.
"""

from __future__ import annotations

from typing import Any

import pytest

from gdrive_export.docs_md_renderer import render_doc_markdown
from gdrive_export.md_rewrite import ImageResolution

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _ok(obj_id: str, rel_path: str) -> ImageResolution:
    return ImageResolution(kind="ok", object_id=obj_id, rel_path=rel_path, alt=None)


def _failed(obj_id: str) -> ImageResolution:
    return ImageResolution(kind="failed", object_id=obj_id, rel_path=None, alt=None)


def _oversized(obj_id: str) -> ImageResolution:
    return ImageResolution(kind="oversized", object_id=obj_id, rel_path=None, alt=None)


def _text_run(
    text: str,
    *,
    bold: bool = False,
    italic: bool = False,
) -> dict[str, Any]:
    style: dict[str, Any] = {}
    if bold:
        style["bold"] = True
    if italic:
        style["italic"] = True
    element: dict[str, Any] = {"textRun": {"content": text}}
    if style:
        element["textRun"]["textStyle"] = style
    return element


def _inline_obj_element(obj_id: str) -> dict[str, Any]:
    return {"inlineObjectElement": {"inlineObjectId": obj_id}}


def _paragraph(
    elements: list[dict[str, Any]],
    style: str = "NORMAL_TEXT",
    bullet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    para: dict[str, Any] = {
        "paragraph": {
            "elements": elements,
            "paragraphStyle": {"namedStyleType": style},
        }
    }
    if bullet is not None:
        para["paragraph"]["bullet"] = bullet
    return para


def _doc(
    content: list[dict[str, Any]],
    positioned: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"body": {"content": content}}
    if positioned:
        result["positionedObjects"] = positioned
    return result


def _positioned_object(obj_id: str, content_uri: str) -> dict[str, Any]:
    return {
        obj_id: {
            "positionedObjectProperties": {
                "embeddedObject": {
                    "imageProperties": {"contentUri": content_uri}
                }
            }
        }
    }


# ---------------------------------------------------------------------------
# Plain text / empty doc
# ---------------------------------------------------------------------------


class TestEmptyDoc:
    """AC-13: empty doc → empty/blank markdown, no error."""

    def test_empty_doc_returns_empty_string(self) -> None:
        result = render_doc_markdown({}, {}, "src1")
        assert result == ""

    def test_doc_no_content_returns_empty(self) -> None:
        doc = _doc([])
        result = render_doc_markdown(doc, {}, "src1")
        assert result == ""

    def test_empty_paragraph_skipped(self) -> None:
        doc = _doc([_paragraph([_text_run("")])])
        result = render_doc_markdown(doc, {}, "src1")
        assert result.strip() == ""


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------


class TestHeadings:
    """AC-6 subset: HEADING_1..6 → # .. ######."""

    @pytest.mark.parametrize(("level", "prefix"), [
        ("HEADING_1", "# "),
        ("HEADING_2", "## "),
        ("HEADING_3", "### "),
        ("HEADING_4", "#### "),
        ("HEADING_5", "##### "),
        ("HEADING_6", "###### "),
        ("TITLE", "# "),
        ("SUBTITLE", "## "),
        ("NORMAL_TEXT", ""),
    ])
    def test_heading_prefix(self, level: str, prefix: str) -> None:
        doc = _doc([_paragraph([_text_run("Hello")], style=level)])
        result = render_doc_markdown(doc, {}, "src1")
        assert result.strip().startswith(prefix + "Hello") or (
            prefix == "" and result.strip() == "Hello"
        )


# ---------------------------------------------------------------------------
# Text formatting
# ---------------------------------------------------------------------------


class TestTextFormatting:
    """Bold, italic, links in textRun."""

    def test_bold_text(self) -> None:
        doc = _doc([_paragraph([_text_run("bold text", bold=True)])])
        result = render_doc_markdown(doc, {}, "src1")
        assert "**bold text**" in result

    def test_italic_text(self) -> None:
        doc = _doc([_paragraph([_text_run("italic text", italic=True)])])
        result = render_doc_markdown(doc, {}, "src1")
        assert "*italic text*" in result

    def test_plain_text_passthrough(self) -> None:
        doc = _doc([_paragraph([_text_run("plain text")])])
        result = render_doc_markdown(doc, {}, "src1")
        assert "plain text" in result

    def test_link_in_text_run(self) -> None:
        element: dict[str, Any] = {
            "textRun": {
                "content": "click here",
                "textStyle": {"link": {"url": "https://example.com"}},
            }
        }
        doc = _doc([_paragraph([element])])
        result = render_doc_markdown(doc, {}, "src1")
        assert "[click here](https://example.com)" in result


# ---------------------------------------------------------------------------
# Bullets
# ---------------------------------------------------------------------------


class TestBullets:
    """AC-6 subset: unordered and ordered lists."""

    def test_unordered_bullet(self) -> None:
        bullet = {"listId": "lst1", "nestingLevel": 0}
        doc = _doc([_paragraph([_text_run("item one")], bullet=bullet)])
        result = render_doc_markdown(doc, {}, "src1")
        assert "- item one" in result

    def test_ordered_bullet_decimal_glyph(self) -> None:
        bullet = {
            "listId": "lst1",
            "nestingLevel": 0,
            "textStyle": {},
        }
        doc = _doc(
            [_paragraph([_text_run("first item")], bullet=bullet)],
        )
        # Without glyphType info, we cannot distinguish; bullet should emit - or 1.
        result = render_doc_markdown(doc, {}, "src1")
        assert ("- first item" in result) or ("1. first item" in result)


# ---------------------------------------------------------------------------
# inlineObject → sidecar ref (AC-4)
# ---------------------------------------------------------------------------


class TestInlineObjects:
    """AC-4: inlineObject elements resolved to sidecar path via resolutions map."""

    def test_inline_object_ok(self) -> None:
        doc = _doc([_paragraph([_inline_obj_element("kix.a")])])
        resolutions = {"kix.a": _ok("kix.a", "my-doc.images/abc123def456.png")}
        result = render_doc_markdown(doc, resolutions, "src1")
        assert "![" in result
        assert "my-doc.images/abc123def456.png" in result

    def test_inline_object_failed_placeholder(self) -> None:
        doc = _doc([_paragraph([_inline_obj_element("kix.a")])])
        resolutions = {"kix.a": _failed("kix.a")}
        result = render_doc_markdown(doc, resolutions, "src1")
        assert "#image-failed-kix.a" in result

    def test_inline_object_oversized_placeholder(self) -> None:
        doc = _doc([_paragraph([_inline_obj_element("kix.a")])])
        resolutions = {"kix.a": _oversized("kix.a")}
        result = render_doc_markdown(doc, resolutions, "src1")
        assert "#oversized-kix.a" in result

    def test_inline_object_missing_from_resolutions_skipped(self) -> None:
        # objectId in body but not in resolutions → should not crash, skip or placeholder
        doc = _doc([_paragraph([_inline_obj_element("kix.x")])])
        result = render_doc_markdown(doc, {}, "src1")
        # Must not raise; content may omit or include fallback
        assert isinstance(result, str)

    def test_inline_object_at_correct_position(self) -> None:
        # Two inline objects in order; check both appear in output in order.
        doc = _doc([
            _paragraph([_inline_obj_element("kix.a")]),
            _paragraph([_text_run("between")]),
            _paragraph([_inline_obj_element("kix.b")]),
        ])
        resolutions = {
            "kix.a": _ok("kix.a", "doc.images/aaaaaaaaaaaa.png"),
            "kix.b": _ok("kix.b", "doc.images/bbbbbbbbbbbb.png"),
        }
        result = render_doc_markdown(doc, resolutions, "src1")
        pos_a = result.find("aaaaaaaaaaaa.png")
        pos_b = result.find("bbbbbbbbbbbb.png")
        assert pos_a >= 0
        assert pos_b >= 0
        assert pos_a < pos_b


# ---------------------------------------------------------------------------
# positionedObjects → appended to body (AC-1 refactor)
# ---------------------------------------------------------------------------


class TestPositionedObjects:
    """positionedObjects resolved and emitted (core of the refactor)."""

    def test_positioned_object_ok(self) -> None:
        doc = _doc(
            content=[_paragraph([_text_run("text")])],
            positioned=_positioned_object("kix.pos1", "https://lh3.googleusercontent.com/p1"),
        )
        resolutions = {"kix.pos1": _ok("kix.pos1", "doc.images/pos1pos1pos1.png")}
        result = render_doc_markdown(doc, resolutions, "src1")
        assert "doc.images/pos1pos1pos1.png" in result

    def test_positioned_object_failed_placeholder(self) -> None:
        doc = _doc(
            content=[_paragraph([_text_run("text")])],
            positioned=_positioned_object("kix.pos1", "https://lh3.googleusercontent.com/p1"),
        )
        resolutions = {"kix.pos1": _failed("kix.pos1")}
        result = render_doc_markdown(doc, resolutions, "src1")
        assert "#image-failed-kix.pos1" in result

    def test_positioned_object_not_in_resolutions_skipped(self) -> None:
        doc = _doc(
            content=[_paragraph([_text_run("text")])],
            positioned=_positioned_object("kix.pos1", "https://lh3.googleusercontent.com/p1"),
        )
        result = render_doc_markdown(doc, {}, "src1")
        assert isinstance(result, str)

    def test_mixed_inline_and_positioned(self) -> None:
        doc = _doc(
            content=[_paragraph([_inline_obj_element("kix.inline")])],
            positioned=_positioned_object("kix.pos", "https://lh3.googleusercontent.com/p"),
        )
        resolutions = {
            "kix.inline": _ok("kix.inline", "doc.images/inlineinline.png"),
            "kix.pos": _ok("kix.pos", "doc.images/pospospospos.png"),
        }
        result = render_doc_markdown(doc, resolutions, "src1")
        assert "doc.images/inlineinline.png" in result
        assert "doc.images/pospospospos.png" in result


# ---------------------------------------------------------------------------
# Table — best-effort pipe markdown
# ---------------------------------------------------------------------------


class TestTable:
    """AC-6 subset: table → best-effort pipe markdown."""

    def test_table_rendered_as_pipe(self) -> None:
        table_row_cells = [
            {"tableCells": [
                {"content": [_paragraph([_text_run("Col A")])]},
                {"content": [_paragraph([_text_run("Col B")])]},
            ]},
        ]
        doc: dict[str, Any] = {
            "body": {
                "content": [{"table": {"tableRows": table_row_cells}}]
            }
        }
        result = render_doc_markdown(doc, {}, "src1")
        assert "|" in result
        assert "Col A" in result
        assert "Col B" in result


# ---------------------------------------------------------------------------
# Ordered list of objectIds returned
# ---------------------------------------------------------------------------


class TestReturnedObjectIds:
    """render_doc_markdown tracks objectIds for caller (used by extract_inline_objects)."""

    def test_inline_ids_in_returned_markdown(self) -> None:
        # The renderer must embed ids into the markdown; caller doesn't need a separate list.
        doc = _doc([
            _paragraph([_inline_obj_element("kix.a")]),
            _paragraph([_inline_obj_element("kix.b")]),
        ])
        resolutions = {
            "kix.a": _ok("kix.a", "doc.images/aaaa00000000.png"),
            "kix.b": _ok("kix.b", "doc.images/bbbb00000000.png"),
        }
        result = render_doc_markdown(doc, resolutions, "src1")
        assert "aaaa00000000.png" in result
        assert "bbbb00000000.png" in result
