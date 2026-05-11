"""Structured Markdown renderer for Google Slides presentations.

Provides render_presentation_markdown, extract_slide_text, extract_speaker_notes.
No Drive API imports (firewall AC-14).
"""

from __future__ import annotations

import unicodedata
from typing import Any

from gdrive_export.normalize import normalize_body


def _normalize_slide_text(text: str) -> str:
    """Normalize slide/notes text: NFKC + strip C0 controls + strip Cf (zero-width) chars.

    LOW-8: mirrors the Cf-strip applied in gsheet_renderer._escape_cell so both
    renderers share the same normalization level for all extracted text.
    """
    nfkc = normalize_body(text)
    return "".join(ch for ch in nfkc if unicodedata.category(ch) != "Cf")


def extract_slide_text(slide: dict[str, Any]) -> str:
    """Extract and concatenate text from visible shape elements in a slide.

    AC-9: shapes sorted by translateY ASC then translateX ASC (top-to-bottom,
    left-to-right). Text from each shape's textElements is concatenated.
    Returns empty string when no text shapes found.
    """
    page_elements: list[dict[str, Any]] = slide.get("pageElements", [])

    shapes_with_pos: list[tuple[float, float, str]] = []
    for element in page_elements:
        if "shape" not in element:
            continue
        shape = element["shape"]
        text_obj = shape.get("text", {})
        text_elements: list[dict[str, Any]] = text_obj.get("textElements", [])
        raw_text = "".join(
            te["textRun"]["content"]
            for te in text_elements
            if "textRun" in te
        )
        if not raw_text:
            continue
        transform = element.get("transform", {})
        translate_y = float(transform.get("translateY", 0.0))
        translate_x = float(transform.get("translateX", 0.0))
        shapes_with_pos.append((translate_y, translate_x, raw_text))

    shapes_with_pos.sort(key=lambda t: (t[0], t[1]))
    return "\n\n".join(_normalize_slide_text(text) for _, _, text in shapes_with_pos)


def extract_speaker_notes(slide: dict[str, Any]) -> str:
    """Extract speaker notes text from slideProperties.notesPage.

    AC-11: returns empty string when no notes page or no text content.
    """
    slide_props: dict[str, Any] = slide.get("slideProperties", {})
    notes_page: dict[str, Any] = slide_props.get("notesPage", {})
    page_elements: list[dict[str, Any]] = notes_page.get("pageElements", [])

    parts: list[str] = []
    for element in page_elements:
        if "shape" not in element:
            continue
        text_obj = element["shape"].get("text", {})
        text_elements: list[dict[str, Any]] = text_obj.get("textElements", [])
        # LOW-4: join multiple textRuns with '\n' so multi-shape notes don't run together.
        runs = [
            te["textRun"]["content"]
            for te in text_elements
            if "textRun" in te
        ]
        raw = "\n".join(runs)
        if raw.strip():
            parts.append(_normalize_slide_text(raw))

    # LOW-4: join across shapes with '\n' as well, keeping single blockquote prefix intact.
    return "\n".join(parts)


def render_presentation_markdown(
    title: str,
    presentation: dict[str, Any],
    file_id: str | None = None,
) -> tuple[str, list[int]]:
    """Render a presentation as Markdown.

    AC-9: each visible slide → ## Slide <N> (1-indexed, continuous).
    AC-11: speaker notes → > **Notes**: <text> block (if non-empty).
    AC-16: slides with pageProperties.skipSlide == True are skipped;
           their 1-based original indices are returned in the hidden list.
    AC-17: when file_id provided, first line is HTML comment.

    Returns (body_markdown, hidden_original_indices).
    """
    slides: list[dict[str, Any]] = presentation.get("slides", [])
    hidden_indices: list[int] = []
    parts: list[str] = []

    if file_id is not None:
        # AC-17: HTML traceability comment, no tab= field for gslide
        parts.append(f"<!-- gdrive: file={file_id} -->")
        parts.append("")

    visible_count = 0
    for original_idx, slide in enumerate(slides, start=1):
        page_props: dict[str, Any] = slide.get("pageProperties", {})
        if page_props.get("skipSlide") is True:
            # AC-16: log info collected; caller emits the warning log
            hidden_indices.append(original_idx)
            continue

        visible_count += 1
        parts.append(f"## Slide {visible_count}")
        parts.append("")

        text = extract_slide_text(slide)
        if text:
            parts.append(text)
        else:
            parts.append("*(no text content)*")

        notes = extract_speaker_notes(slide)
        if notes:
            parts.append("")
            # LOW-4: prefix every line with '> ' so multi-line notes remain valid blockquote.
            note_lines = notes.splitlines()
            first_line = f"> **Notes**: {note_lines[0]}"
            continuation = [f"> {line}" for line in note_lines[1:]]
            parts.append("\n".join([first_line, *continuation]))

        parts.append("")

    return "\n".join(parts), hidden_indices
