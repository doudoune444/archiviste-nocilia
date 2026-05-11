"""Unit tests for gdrive_export.gslide_renderer — AC-9/11/16/17 + Y/X ordering."""

from __future__ import annotations

from gdrive_export.gslide_renderer import (
    extract_slide_text,
    extract_speaker_notes,
    render_presentation_markdown,
)


def _make_text_element(content: str) -> dict[str, object]:
    return {"textRun": {"content": content}}


def _make_shape(
    text_content: str,
    translate_y: float = 0.0,
    translate_x: float = 0.0,
) -> dict[str, object]:
    return {
        "shape": {
            "text": {"textElements": [_make_text_element(text_content)]},
        },
        "transform": {"translateY": translate_y, "translateX": translate_x},
    }


def _make_slide(
    shapes: list[dict[str, object]],
    notes_text: str = "",
    *,
    skip_slide: bool = False,
) -> dict[str, object]:
    page_elements = list(shapes)
    slide: dict[str, object] = {"pageElements": page_elements}
    props: dict[str, object] = {}
    if skip_slide:
        props["skipSlide"] = True
    slide["pageProperties"] = props
    if notes_text:
        notes_shape = _make_shape(notes_text)
        slide["slideProperties"] = {
            "notesPage": {
                "pageElements": [notes_shape],
            }
        }
    return slide


class TestExtractSlideText:
    """AC-9: shapes sorted Y ASC then X ASC, text concatenated."""

    def test_single_shape(self) -> None:
        shape = _make_shape("Hello")
        slide = _make_slide([shape])
        assert extract_slide_text(slide) == "Hello"

    def test_top_to_bottom_order(self) -> None:
        # AC-9: higher Y = lower on page, extracted second
        top_shape = _make_shape("Top", translate_y=10.0, translate_x=0.0)
        bottom_shape = _make_shape("Bottom", translate_y=100.0, translate_x=0.0)
        slide = _make_slide([bottom_shape, top_shape])  # reversed input order
        assert extract_slide_text(slide) == "Top\n\nBottom"

    def test_left_to_right_order(self) -> None:
        # AC-9: same Y → sort by X ASC (left before right)
        left = _make_shape("Left", translate_y=0.0, translate_x=10.0)
        right = _make_shape("Right", translate_y=0.0, translate_x=100.0)
        slide = _make_slide([right, left])  # reversed
        assert extract_slide_text(slide) == "Left\n\nRight"

    def test_no_text_shapes(self) -> None:
        # Failure mode: slide with no shapes → empty
        slide: dict[str, object] = {"pageElements": [], "pageProperties": {}}
        assert extract_slide_text(slide) == ""

    def test_non_shape_elements_skipped(self) -> None:
        # AC-9: only 'shape' type elements are text sources
        image_element: dict[str, object] = {
            "image": {},
            "transform": {"translateY": 0.0, "translateX": 0.0},
        }
        shape = _make_shape("Text")
        slide = _make_slide([shape])
        slide["pageElements"] = [image_element, shape]
        assert extract_slide_text(slide) == "Text"


class TestExtractSpeakerNotes:
    """AC-11: speaker notes extracted from slideProperties.notesPage."""

    def test_notes_present(self) -> None:
        # AC-11: notes shape text extracted
        slide = _make_slide([_make_shape("body")], notes_text="Speaker note here")
        assert extract_speaker_notes(slide) == "Speaker note here"

    def test_no_notes_returns_empty(self) -> None:
        # AC-11: no slideProperties.notesPage → empty string
        slide = _make_slide([_make_shape("body")])
        assert extract_speaker_notes(slide) == ""

    def test_empty_notes_returns_empty(self) -> None:
        # AC-11: notes page exists but all text elements empty
        slide = _make_slide([_make_shape("body")], notes_text="")
        assert extract_speaker_notes(slide) == ""

    def test_multi_textrun_notes_joined_with_newline(self) -> None:
        # LOW-4: multiple textRuns in notes must be joined with '\n' (not '')
        # to prevent words from running together in the blockquote.
        multi_run_shape: dict[str, object] = {
            "shape": {
                "text": {
                    "textElements": [
                        {"textRun": {"content": "First run"}},
                        {"textRun": {"content": "Second run"}},
                    ]
                }
            },
            "transform": {"translateY": 0.0, "translateX": 0.0},
        }
        slide: dict[str, object] = {
            "pageElements": [_make_shape("body")],
            "pageProperties": {},
            "slideProperties": {
                "notesPage": {"pageElements": [multi_run_shape]}
            },
        }
        result = extract_speaker_notes(slide)
        # Runs must be separated, not concatenated
        assert "First run" in result
        assert "Second run" in result
        assert result != "First runSecond run"

    def test_multi_shape_notes_blockquote_valid(self) -> None:
        # LOW-4: multi-shape notes must produce valid blockquote (each line prefixed '>')
        slide_with_two_note_shapes: dict[str, object] = {
            "pageElements": [_make_shape("body")],
            "pageProperties": {},
            "slideProperties": {
                "notesPage": {
                    "pageElements": [
                        _make_shape("Line one"),
                        _make_shape("Line two"),
                    ]
                }
            },
        }
        presentation = {"slides": [slide_with_two_note_shapes]}
        body, _ = render_presentation_markdown("Deck", presentation)
        # Every line after "> **Notes**:" prefix must also start with ">" if non-empty
        in_notes = False
        for line in body.splitlines():
            if line.startswith("> **Notes**:"):
                in_notes = True
                continue
            if in_notes and line.strip():
                assert line.startswith(">"), f"Non-blockquote notes line: {line!r}"
            else:
                in_notes = False


class TestRenderPresentationMarkdown:
    """AC-9/10/11/16/17: full presentation rendering."""

    def test_three_slides_numbered(self) -> None:
        # AC-9: 3 visible slides → ## Slide 1, ## Slide 2, ## Slide 3
        slides = [
            _make_slide([_make_shape("Content 1")]),
            _make_slide([_make_shape("Content 2")]),
            _make_slide([_make_shape("Content 3")]),
        ]
        presentation = {"slides": slides}
        body, hidden = render_presentation_markdown("My Deck", presentation)
        assert "## Slide 1" in body
        assert "## Slide 2" in body
        assert "## Slide 3" in body
        assert hidden == []

    def test_hidden_slide_skipped_renumbered(self) -> None:
        # AC-16: slide 2 (index=1) is hidden → output has Slide 1..4 (4 visible, 1 hidden)
        slides = [
            _make_slide([_make_shape("S1")]),
            _make_slide([_make_shape("hidden")], skip_slide=True),
            _make_slide([_make_shape("S3")]),
            _make_slide([_make_shape("S4")]),
            _make_slide([_make_shape("S5")]),
        ]
        presentation = {"slides": slides}
        body, hidden = render_presentation_markdown("Deck", presentation)
        assert "## Slide 1" in body
        assert "## Slide 2" in body
        assert "## Slide 3" in body
        assert "## Slide 4" in body
        assert "## Slide 5" not in body
        assert hidden == [2]  # 1-based original index of hidden slide

    def test_notes_rendered_as_blockquote(self) -> None:
        # AC-11: speaker notes → > **Notes**: <text>
        slide = _make_slide([_make_shape("body")], notes_text="Important note")
        presentation = {"slides": [slide]}
        body, _ = render_presentation_markdown("Deck", presentation)
        assert "> **Notes**: Important note" in body

    def test_no_notes_no_blockquote(self) -> None:
        # AC-11: slide without notes → no blockquote
        slide = _make_slide([_make_shape("body")])
        presentation = {"slides": [slide]}
        body, _ = render_presentation_markdown("Deck", presentation)
        assert "> **Notes**:" not in body

    def test_no_text_content_placeholder(self) -> None:
        # Failure mode: slide with no text → *(no text content)*
        slide: dict[str, object] = {"pageElements": [], "pageProperties": {}}
        presentation = {"slides": [slide]}
        body, _ = render_presentation_markdown("Deck", presentation)
        assert "*(no text content)*" in body

    def test_html_comment_first_line(self) -> None:
        # AC-17: first line of body = <!-- gdrive: file=<file_id> -->
        slide = _make_slide([_make_shape("text")])
        presentation = {"slides": [slide]}
        body, _ = render_presentation_markdown("Deck", presentation, file_id="slide123")
        first_line = body.split("\n")[0]
        assert first_line == "<!-- gdrive: file=slide123 -->"

    def test_html_comment_no_tab_field(self) -> None:
        # AC-17: gslide comment has no tab= field
        slide = _make_slide([_make_shape("text")])
        presentation = {"slides": [slide]}
        body, _ = render_presentation_markdown("Deck", presentation, file_id="sid1")
        assert "tab=" not in body.split("\n")[0]

    def test_empty_slides_list(self) -> None:
        # Edge: empty presentation
        body, hidden = render_presentation_markdown("Deck", {"slides": []})
        assert hidden == []
        assert "## Slide" not in body
