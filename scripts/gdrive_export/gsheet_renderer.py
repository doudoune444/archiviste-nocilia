"""GFM table renderer for Google Sheets tabs.

Provides render_tab_markdown, build_tab_source_id, build_tab_title,
and resolve_tab_collisions. No Drive API imports (firewall AC-14).
"""

from __future__ import annotations

import unicodedata
from typing import Any

from gdrive_export.normalize import normalize_body
from gdrive_export.slugify import slugify

_EM_DASH = "—"


def build_tab_source_id(file_id: str, gid: int) -> str:
    """Return the state key for a gsheet tab: '<file_id>#<gid_decimal>'.

    AC-5: gid is a plain decimal integer, no prefix, no padding.
    """
    return f"{file_id}#{gid}"


def build_tab_title(workbook_title: str, tab_title: str) -> str:
    """Return the frontmatter title for a gsheet tab.

    AC-6: format is '<workbook_title> — <tab_title>' with U+2014 em-dash + spaces.
    """
    return f"{workbook_title} {_EM_DASH} {tab_title}"


def resolve_tab_collisions(
    tabs: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], str]]:
    """Assign slugs to tabs, suffixing collisions with -<gid>.

    AC-4: order by 'index' field (ascending). First tab that produces a slug
    keeps it; subsequent duplicates get '-<sheetId>' appended.

    Returns a list of (tab_dict, slug) pairs in index order.
    """
    sorted_tabs = sorted(tabs, key=lambda t: int(t["index"]))
    seen: set[str] = set()
    result: list[tuple[dict[str, Any], str]] = []
    for tab in sorted_tabs:
        title = str(tab["title"])
        gid = int(tab["sheetId"])
        base_slug = slugify(title, str(gid))
        if base_slug not in seen:
            seen.add(base_slug)
            result.append((tab, base_slug))
        else:
            result.append((tab, f"{base_slug}-{gid}"))
    return result


def render_tab_markdown(
    workbook_title: str,
    tab_title: str,
    values: list[list[str]],
    file_id: str | None = None,
    gid: int | None = None,
) -> str:
    """Render a gsheet tab as a GFM Markdown string.

    AC-2: first row = header, remaining rows = data, cells pipe-escaped.
    AC-12: NFKC normalise + strip control chars applied to all cell text.
    AC-17: when file_id provided, body starts with HTML comment.

    Returns the full body string (no YAML frontmatter).
    """
    parts: list[str] = []

    if file_id is not None and gid is not None:
        # AC-17: HTML traceability comment, first line of body
        parts.append(f"<!-- gdrive: file={file_id} tab={gid} -->")
        parts.append("")

    if not values:
        parts.append("*(empty sheet)*")
        return "\n".join(parts)

    header = values[0]
    rows = values[1:]

    header_cells = [_escape_cell(c) for c in header]
    parts.append("| " + " | ".join(header_cells) + " |")
    parts.append("| " + " | ".join("---" for _ in header_cells) + " |")

    for row in rows:
        padded = list(row) + [""] * max(0, len(header) - len(row))
        escaped = [_escape_cell(c) for c in padded[: len(header)]]
        parts.append("| " + " | ".join(escaped) + " |")

    return "\n".join(parts)


def _escape_cell(text: str) -> str:
    r"""Normalise cell text and escape GFM table special characters.

    AC-12: NFKC + strip C0 control chars + strip zero-width format chars.
    AC-2: '|' escaped to '\|', newlines replaced with '<br>'.
    """
    normalised = normalize_body(text)
    # Strip Unicode zero-width / invisible format characters (Cf category).
    without_zw = "".join(
        ch for ch in normalised
        if unicodedata.category(ch) != "Cf"
    )
    return without_zw.replace("|", r"\|").replace("\n", "<br>")
