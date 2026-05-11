"""Unit tests for gdrive_export.gsheet_renderer — AC-2/3/4/5/6/8/12/13/17."""

from __future__ import annotations

import hashlib

from gdrive_export.gsheet_renderer import (
    build_tab_source_id,
    build_tab_title,
    render_tab_markdown,
    resolve_tab_collisions,
)


class TestRenderTabMarkdown:
    """AC-2: GFM table rendering, escape rules, empty sheet."""

    def test_header_row_and_separator(self) -> None:
        # AC-2: first row = header, second row = |---|
        values = [["Name", "Age"], ["Alice", "30"], ["Bob", "25"]]
        md = render_tab_markdown("Book", "Sheet1", values)
        assert "| Name | Age |" in md
        assert "|---|" in md or "| --- |" in md

    def test_pipe_escaped_in_cells(self) -> None:
        # AC-2: '|' in cell content must be escaped to '\|'
        values = [["Col"], ["a|b"]]
        md = render_tab_markdown("Book", "Sheet1", values)
        assert r"a\|b" in md

    def test_newline_in_cell_becomes_br(self) -> None:
        # AC-2: '\n' in cell content must become '<br>'
        values = [["Col"], ["line1\nline2"]]
        md = render_tab_markdown("Book", "Sheet1", values)
        assert "line1<br>line2" in md

    def test_empty_sheet_body(self) -> None:
        # AC-2 failure mode: empty sheet → *(empty sheet)*
        md = render_tab_markdown("Book", "Empty", [])
        assert "*(empty sheet)*" in md

    def test_single_row_treated_as_header_only(self) -> None:
        # AC-2: single row = header row, no data rows
        values = [["Col1", "Col2"]]
        md = render_tab_markdown("Book", "Sheet1", values)
        assert "| Col1 | Col2 |" in md

    def test_merged_header_pads_to_widest_row(self) -> None:
        # Merged cell on header row: Sheets API returns ["Title"] (trimmed),
        # data rows have full width. Table must pad header to max row width
        # instead of truncating data rows to header length.
        values = [["Titre"], ["a", "b", "c", "d"], ["e", "f", "g", "h"]]
        md = render_tab_markdown("Book", "Sheet1", values)
        assert "| Titre |  |  |  |" in md
        assert "| --- | --- | --- | --- |" in md
        assert "| a | b | c | d |" in md
        assert "| e | f | g | h |" in md

    def test_jagged_rows_pad_to_widest(self) -> None:
        # Non-uniform row widths: pad all to max(len(row)) across all rows.
        values = [["A", "B"], ["x"], ["y", "z", "w"]]
        md = render_tab_markdown("Book", "Sheet1", values)
        assert "| A | B |  |" in md
        assert "| x |  |  |" in md
        assert "| y | z | w |" in md

    def test_html_comment_first_line(self) -> None:
        # AC-17: first line of body (before table) is HTML comment with file_id and tab gid
        values = [["H"]]
        md = render_tab_markdown("Book", "Sheet1", values, file_id="fileid123", gid=42)
        first_line = md.split("\n")[0]
        assert first_line == "<!-- gdrive: file=fileid123 tab=42 -->"

    def test_html_comment_without_file_id(self) -> None:
        # AC-17: when file_id not provided, no HTML comment emitted
        values = [["H"]]
        md = render_tab_markdown("Book", "Sheet1", values)
        assert not md.startswith("<!-- gdrive:")

    def test_nfkc_normalize_applied(self) -> None:
        # AC-12: NFKC + control char strip applied to cell values
        values = [["Col"], ["caf\xe9\x00val"]]
        md = render_tab_markdown("Book", "Sheet1", values)
        assert "\x00" not in md
        assert "caf" in md

    def test_zero_width_char_stripped(self) -> None:
        # AC-12: zero-width chars (Cf category) stripped
        # U+200B is ZERO WIDTH SPACE, a Cf-category char
        zwsp = "\u200b"
        values = [["Col"], [f"hello{zwsp}world"]]
        md = render_tab_markdown("Book", "Sheet1", values)
        assert "\u200b" not in md
        assert "helloworld" in md


class TestBuildTabSourceId:
    """AC-5: source_id format is <file_id>#<gid_decimal>."""

    def test_format(self) -> None:
        # AC-5: no prefix, no padding
        assert build_tab_source_id("fileABC", 0) == "fileABC#0"

    def test_non_zero_gid(self) -> None:
        # AC-5: gid as decimal integer
        assert build_tab_source_id("fileABC", 1734218910) == "fileABC#1734218910"

    def test_no_zero_padding(self) -> None:
        # AC-5: no zero-padding
        result = build_tab_source_id("x", 7)
        assert result == "x#7"
        assert "#07" not in result


class TestResolveTabCollisions:
    """AC-4: collision resolution with -<gid> suffix."""

    def test_no_collision(self) -> None:
        # AC-4: unique slugs → no suffix
        tabs = [
            {"title": "Data 2024", "sheetId": 0, "index": 0},
            {"title": "Summary", "sheetId": 1, "index": 1},
        ]
        result = resolve_tab_collisions(tabs)
        slugs = [slug for _, slug in result]
        assert "data-2024" in slugs
        assert "summary" in slugs

    def test_collision_second_gets_gid_suffix(self) -> None:
        # AC-4: two tabs with same slug → second (by index) gets -<gid>
        tabs = [
            {"title": "Donn\xe9es 2024", "sheetId": 100, "index": 0},
            {"title": "donnees-2024", "sheetId": 200, "index": 1},
        ]
        result = resolve_tab_collisions(tabs)
        slugs = [slug for _, slug in result]
        assert slugs[0] == "donnees-2024"
        assert slugs[1] == "donnees-2024-200"

    def test_order_by_index(self) -> None:
        # AC-4: order = Drive sheet index, lowest wins
        tabs = [
            {"title": "dup", "sheetId": 999, "index": 0},
            {"title": "dup", "sheetId": 111, "index": 1},
        ]
        result = resolve_tab_collisions(tabs)
        assert result[0][1] == "dup"
        assert result[1][1] == "dup-111"


class TestContentSignature:
    """AC-8: sha256 of rendered markdown is stable."""

    def test_sha256_stable(self) -> None:
        # AC-8: same content → same hash
        values = [["A", "B"], ["1", "2"]]
        md1 = render_tab_markdown("Book", "Tab", values)
        md2 = render_tab_markdown("Book", "Tab", values)
        h1 = hashlib.sha256(md1.encode("utf-8")).hexdigest()
        h2 = hashlib.sha256(md2.encode("utf-8")).hexdigest()
        assert h1 == h2

    def test_different_content_different_hash(self) -> None:
        # AC-8: different values → different hash
        md1 = render_tab_markdown("Book", "Tab", [["A"], ["1"]])
        md2 = render_tab_markdown("Book", "Tab", [["A"], ["2"]])
        h1 = hashlib.sha256(md1.encode("utf-8")).hexdigest()
        h2 = hashlib.sha256(md2.encode("utf-8")).hexdigest()
        assert h1 != h2


class TestFrontmatterTitle:
    """AC-6: frontmatter title uses em-dash U+2014."""

    def test_title_format(self) -> None:
        # AC-6: title = "<workbook_title> — <tab_title>" (em-dash U+2014)
        title = build_tab_title("My Workbook", "Sheet 1")
        # em-dash U+2014
        assert title == "My Workbook — Sheet 1"
        assert "—" in title
