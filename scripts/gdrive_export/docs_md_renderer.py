"""Pure-function renderer: Docs API document dict → Markdown text.

Walks body.content structural elements in order, then positionedObjects.
Inline image refs are resolved directly via the supplied resolutions map,
producing final sidecar paths. No second-pass regex rewrite needed.

AC-4: each inlineObjectElement emits ![alt](sidecar/path) using resolutions.
AC-6 subset: NORMAL_TEXT / HEADING_1..6 / TITLE / SUBTITLE / bullets /
  bold / italic / links / tables (best-effort pipe). Unsupported constructs
  from the original export path are N/A here (we generate directly).

positionedObjects: anchored images that Drive markdown export cannot see.
  After body traversal, all positioned object ids are emitted at end of doc.

No Drive API imports — complies with ING-013 conftest firewall.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gdrive_export.md_rewrite import ImageResolution

# Mapping from Docs API namedStyleType to Markdown heading prefix.
_HEADING_PREFIX: dict[str, str] = {
    "HEADING_1": "# ",
    "TITLE": "# ",
    "HEADING_2": "## ",
    "SUBTITLE": "## ",
    "HEADING_3": "### ",
    "HEADING_4": "#### ",
    "HEADING_5": "##### ",
    "HEADING_6": "###### ",
}


@dataclass
class _RenderState:
    """Mutable state threaded through recursive element rendering."""

    resolutions: dict[str, ImageResolution]
    source_id: str
    output_lines: list[str] = field(default_factory=list)


def render_doc_markdown(
    doc: dict[str, Any],
    object_id_to_sidecar_path: dict[str, ImageResolution],
    source_id: str,
) -> str:
    """Walk a Docs API document dict and return markdown text.

    Inline and positioned image refs are resolved via object_id_to_sidecar_path.
    Returns empty string for an empty or missing body.
    """
    state = _RenderState(
        resolutions=object_id_to_sidecar_path,
        source_id=source_id,
    )
    content: list[dict[str, Any]] = doc.get("body", {}).get("content", [])
    for structural_element in content:
        _render_structural_element(structural_element, state)

    _render_positioned_objects(doc.get("positionedObjects", {}), state)

    return "\n".join(state.output_lines).strip()


def _render_structural_element(element: dict[str, Any], state: _RenderState) -> None:
    """Dispatch a top-level structural element to its renderer."""
    if "paragraph" in element:
        _render_paragraph(element["paragraph"], state)
    elif "table" in element:
        _render_table(element["table"], state)
    # sectionBreak, tableOfContents: skip silently.


def _render_paragraph(paragraph: dict[str, Any], state: _RenderState) -> None:
    """Render a paragraph element to one or more output lines."""
    style_type: str = (
        paragraph.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
    )
    prefix = _HEADING_PREFIX.get(style_type, "")
    bullet = paragraph.get("bullet")
    if bullet is not None:
        prefix = "- "

    parts: list[str] = []
    for el in paragraph.get("elements", []):
        part = _render_element(el, state)
        if part:
            parts.append(part)

    text = "".join(parts).rstrip("\n")
    if text.strip():
        state.output_lines.append(f"{prefix}{text}")
    elif not parts:
        # Empty paragraph — emit blank line to preserve spacing.
        state.output_lines.append("")


def _render_element(element: dict[str, Any], state: _RenderState) -> str:
    """Render a paragraph element and return its text fragment."""
    if "textRun" in element:
        return _render_text_run(element["textRun"])
    if "inlineObjectElement" in element:
        return _render_inline_object(element["inlineObjectElement"], state)
    if "pageBreak" in element or "horizontalRule" in element:
        state.output_lines.append("\n---\n")
    return ""


def _render_text_run(text_run: dict[str, Any]) -> str:
    """Render a textRun element to a formatted string fragment."""
    content: str = text_run.get("content", "")
    # Soft line break → markdown line break.
    content = content.replace("\x0b", "  \n")
    # Tab passthrough.
    content = content.rstrip("\n")
    if not content:
        return ""

    style: dict[str, Any] = text_run.get("textStyle", {})
    link_url: str | None = (style.get("link") or {}).get("url")
    is_bold: bool = bool(style.get("bold"))
    is_italic: bool = bool(style.get("italic"))

    if link_url:
        return f"[{content}]({link_url})"
    if is_bold and is_italic:
        return f"***{content}***"
    if is_bold:
        return f"**{content}**"
    if is_italic:
        return f"*{content}*"
    return content


def _render_inline_object(
    inline_obj_element: dict[str, Any],
    state: _RenderState,
) -> str:
    """Render an inlineObjectElement to a Markdown image tag."""
    obj_id: str = inline_obj_element.get("inlineObjectId", "")
    return _image_ref(obj_id, state)


def _image_ref(obj_id: str, state: _RenderState) -> str:
    """Return the markdown image tag for an objectId using the resolutions map."""
    resolution = state.resolutions.get(obj_id)
    if resolution is None:
        return ""
    if resolution.kind == "ok" and resolution.rel_path is not None:
        alt = resolution.alt or ""
        return f"![{alt}]({resolution.rel_path})"
    if resolution.kind == "oversized":
        return f"![image trop volumineuse](#oversized-{obj_id})"
    # kind == "failed"
    return f"![image indisponible](#image-failed-{obj_id})"


def _render_table(table: dict[str, Any], state: _RenderState) -> None:
    """Render a table element as best-effort pipe markdown (AC-6 subset)."""
    rows: list[dict[str, Any]] = table.get("tableRows", [])
    for row_index, row in enumerate(rows):
        cells = row.get("tableCells", [])
        cell_texts: list[str] = []
        for cell in cells:
            cell_parts: list[str] = []
            for cell_element in cell.get("content", []):
                if "paragraph" in cell_element:
                    para_parts: list[str] = []
                    for el in cell_element["paragraph"].get("elements", []):
                        part = _render_element(el, state)
                        if part:
                            para_parts.append(part)
                    cell_parts.append("".join(para_parts).strip())
            cell_texts.append(" ".join(cell_parts).strip())
        state.output_lines.append("| " + " | ".join(cell_texts) + " |")
        if row_index == 0:
            state.output_lines.append(
                "| " + " | ".join("---" for _ in cell_texts) + " |"
            )


def _render_positioned_objects(
    positioned: dict[str, Any],
    state: _RenderState,
) -> None:
    """Append positioned object image refs at end of document."""
    for obj_id in positioned:
        ref = _image_ref(obj_id, state)
        if ref:
            state.output_lines.append(ref)
