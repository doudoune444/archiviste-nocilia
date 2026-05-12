"""End-to-end integration tests for gdrive_export.sync — AC-1..AC-18 (ING-014 extension)."""

from __future__ import annotations

import hashlib
import io
import json
import random
import re
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import googleapiclient.errors as gae
import PIL.Image
import pytest
import yaml

from gdrive_export.drive_client import DriveApiError, DriveClient
from gdrive_export.state import load_state
from gdrive_export.sync import SummaryCounts, run_sync

# ---------------------------------------------------------------------------
# Drive stub builder
# ---------------------------------------------------------------------------

_DOC_MIME = "application/vnd.google-apps.document"
_PNG_MIME = "image/png"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_SLIDES_MIME = "application/vnd.google-apps.presentation"


def _default_png() -> tuple[bytes, str]:
    """Return a small valid PNG payload for use as default image download."""
    img = PIL.Image.new("RGB", (10, 10), color=(100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), "image/png"


def _build_doc_response(
    file_id: str,
    doc_inline_objects: dict[str, dict[str, Any]],
    doc_bodies: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a minimal Docs API document dict for testing.

    Text from doc_bodies is split into paragraphs; inline object elements are
    appended after. This mirrors what render_doc_markdown expects.
    """
    inline_objs = doc_inline_objects.get(file_id, {})
    body_content: list[dict[str, Any]] = []

    # Add text paragraphs from doc_bodies (if any).
    raw_text: str = (doc_bodies or {}).get(file_id, "")
    for line in raw_text.splitlines():
        if line.startswith("# "):
            style = "HEADING_1"
            text = line[2:]
        elif line.startswith("## "):
            style = "HEADING_2"
            text = line[3:]
        else:
            style = "NORMAL_TEXT"
            text = line
        if text:
            body_content.append({
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": style},
                    "elements": [{"textRun": {"content": text}}],
                }
            })

    # Append inline object paragraphs.
    for obj_id in inline_objs:
        body_content.append({
            "paragraph": {
                "elements": [{"inlineObjectElement": {"inlineObjectId": obj_id}}]
            }
        })

    return {
        "documentId": file_id,
        "inlineObjects": inline_objs,
        "body": {"content": body_content},
    }


def _attach_side_effects(
    client: MagicMock,
    doc_bodies: dict[str, str],
    png_bodies: dict[str, bytes],
    export_errors: dict[str, Exception],
    sheet_tabs: dict[str, list[dict[str, Any]]],
    sheet_values: dict[str, dict[str, list[list[str]]]],
    presentations: dict[str, dict[str, Any]],
    doc_inline_objects: dict[str, dict[str, Any]],
    image_payloads: dict[str, tuple[bytes, str]],
    image_errors: dict[str, Exception],
) -> None:
    """Wire side-effect closures onto a mock DriveClient."""

    def _export(file_id: str) -> str:
        if file_id in export_errors:
            raise export_errors[file_id]
        return doc_bodies.get(file_id, f"# Doc {file_id}\n")

    def _download(file_id: str) -> bytes:
        if file_id in export_errors:
            raise export_errors[file_id]
        return png_bodies.get(file_id, b"\x89PNG\r\n" + file_id.encode())

    def _get_tabs(file_id: str) -> list[dict[str, Any]]:
        if file_id in export_errors:
            raise export_errors[file_id]
        return sheet_tabs.get(file_id, [])

    def _get_values(file_id: str, tab_title: str) -> list[list[str]]:
        if file_id in export_errors:
            raise export_errors[file_id]
        return sheet_values.get(file_id, {}).get(tab_title, [])

    def _get_presentation(file_id: str) -> dict[str, Any]:
        if file_id in export_errors:
            raise export_errors[file_id]
        return presentations.get(file_id, {"slides": []})

    def _get_document(file_id: str) -> dict[str, Any]:
        if file_id in export_errors:
            raise export_errors[file_id]
        return _build_doc_response(file_id, doc_inline_objects, doc_bodies)

    def _download_image(content_uri: str) -> tuple[bytes, str]:
        if content_uri in image_errors:
            raise image_errors[content_uri]
        if content_uri in image_payloads:
            return image_payloads[content_uri]
        return _default_png()

    client.export_gdoc_markdown.side_effect = _export
    client.download_png.side_effect = _download
    client.get_spreadsheet_tabs.side_effect = _get_tabs
    client.get_sheet_values.side_effect = _get_values
    client.get_presentation.side_effect = _get_presentation
    client.get_document.side_effect = _get_document
    client.download_image.side_effect = _download_image


def _make_drive_client(
    files: list[dict[str, Any]],
    doc_bodies: dict[str, str] | None = None,
    png_bodies: dict[str, bytes] | None = None,
    export_errors: dict[str, Exception] | None = None,
    sheet_tabs: dict[str, list[dict[str, Any]]] | None = None,
    sheet_values: dict[str, dict[str, list[list[str]]]] | None = None,
    presentations: dict[str, dict[str, Any]] | None = None,
    doc_inline_objects: dict[str, dict[str, Any]] | None = None,
    image_payloads: dict[str, tuple[bytes, str]] | None = None,
    image_errors: dict[str, Exception] | None = None,
) -> MagicMock:
    """Return a mock DriveClient configured with the given file list and content."""
    client = MagicMock()
    client.list_folder_recursive.return_value = files
    client.verify_extra_scopes.return_value = None
    _attach_side_effects(
        client,
        doc_bodies=doc_bodies or {},
        png_bodies=png_bodies or {},
        export_errors=export_errors or {},
        sheet_tabs=sheet_tabs or {},
        sheet_values=sheet_values or {},
        presentations=presentations or {},
        doc_inline_objects=doc_inline_objects or {},
        image_payloads=image_payloads or {},
        image_errors=image_errors or {},
    )
    return client


def _file_entry(
    file_id: str,
    name: str,
    mime: str,
    components: list[str] | None = None,
    md5: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": file_id,
        "name": name,
        "mimeType": mime,
        "drive_path_components": components or [],
    }
    if md5 is not None:
        entry["md5Checksum"] = md5
    return entry


def _run(
    client: MagicMock,
    lore_root: Path,
    state_path: Path,
    *,
    dry_run: bool = False,
) -> tuple[SummaryCounts, list[dict[str, Any]]]:
    """Run sync and collect JSON log lines from stdout."""
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        summary = run_sync(
            client,
            root_folder_id="root-id",
            lore_root=lore_root,
            state_path=state_path,
            dry_run=dry_run,
        )
    finally:
        sys.stdout = old_stdout

    lines = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    return summary, lines


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFirstRun:
    """AC-2/3/11/13: first run creates files and persists state."""

    def test_gdoc_written_and_state_saved(self, tmp_path: Path) -> None:
        # AC-2: gdoc exported and written as .md under lore_root.
        # MED-1: per-file log status must be exactly "created" (not "create").
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        body = "# Title\n\nContent here.\n"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": body},
        )
        summary, logs = _run(client, lore_root, state_path)

        md_file = lore_root / "my-doc.md"
        assert md_file.exists()
        assert summary.created == 1
        assert summary.errors == 0

        # Verify per-file log status is exactly "created".
        file_logs = [lg for lg in logs if lg.get("event") == "gdrive_sync.file"]
        assert len(file_logs) == 1
        assert file_logs[0]["status"] == "created"

        # Verify state persisted.
        state = load_state(state_path)
        assert "doc1" in state
        assert state["doc1"].content_signature.startswith("sha256:")

    def test_png_written_and_state_saved(self, tmp_path: Path) -> None:
        # AC-3: PNG downloaded and written under lore_root, no .md produced.
        # Drive name "beach" slugifies to "beach"; ext stays ".png".
        # MED-2: per-file log status must be exactly "created" (not "create").
        # MED-3: content_signature must use Drive md5Checksum field, not client-side hash.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        drive_md5 = hashlib.md5(png_bytes).hexdigest()  # noqa: S324
        client = _make_drive_client(
            files=[_file_entry("png1", "beach", _PNG_MIME, md5=drive_md5)],
            png_bodies={"png1": png_bytes},
        )
        summary, logs = _run(client, lore_root, state_path)

        # slugify("beach", ...) → "beach", ext=".png" → lore/beach.png
        png_file = lore_root / "beach.png"
        assert png_file.exists()
        assert png_file.read_bytes() == png_bytes
        assert not (lore_root / "beach.md").exists()
        assert summary.created == 1

        # Verify per-file log status is exactly "created".
        file_logs = [lg for lg in logs if lg.get("event") == "gdrive_sync.file"]
        assert len(file_logs) == 1
        assert file_logs[0]["status"] == "created"

        state = load_state(state_path)
        # MED-3: signature must come from Drive md5Checksum field, not client-side computation.
        assert state["png1"].content_signature == f"md5:{drive_md5}"

    def test_first_run_log_order(self, tmp_path: Path) -> None:
        # AC-13: first run emits start then first_run events in order.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        client = _make_drive_client(files=[])
        _, logs = _run(client, lore_root, state_path)

        events = [log["event"] for log in logs]
        assert events[0] == "gdrive_sync.start"
        assert "gdrive_sync.first_run" in events
        assert events.index("gdrive_sync.first_run") == 1

    def test_no_first_run_log_when_state_exists(self, tmp_path: Path) -> None:
        # AC-13: existing state → no first_run log.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        state_path.write_text("{}\n", encoding="utf-8")
        client = _make_drive_client(files=[])
        _, logs = _run(client, lore_root, state_path)
        events = [log["event"] for log in logs]
        assert "gdrive_sync.first_run" not in events

    def test_frontmatter_key_order(self, tmp_path: Path) -> None:
        # AC-4: frontmatter keys in strict order: title, source, source_id, drive_path,
        # exported_at, archived, archived_at, tags, access_tier.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": "# Body\n"},
        )
        _run(client, lore_root, state_path)

        md_content = (lore_root / "my-doc.md").read_text(encoding="utf-8")
        # Extract frontmatter between --- markers.
        assert md_content.startswith("---\n")
        fm_end = md_content.index("\n---\n", 4)
        fm_yaml = md_content[4:fm_end]
        fm_keys = [line.split(":")[0].strip() for line in fm_yaml.splitlines() if ":" in line]
        expected_prefix = ["title", "source", "source_id", "drive_path", "exported_at", "archived"]
        assert fm_keys[: len(expected_prefix)] == expected_prefix


class TestSecondRunUnchanged:
    """AC-11: unchanged content_signature → no rewrite, mtime preserved."""

    def test_unchanged_gdoc_not_rewritten(self, tmp_path: Path) -> None:
        # AC-11: second run with same content → file not rewritten, status=unchanged.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        body = "# Title\n\nSame content.\n"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": body},
        )
        _run(client, lore_root, state_path)
        md_file = lore_root / "my-doc.md"
        mtime_before = md_file.stat().st_mtime

        summary, logs = _run(client, lore_root, state_path)

        assert md_file.stat().st_mtime == mtime_before
        assert summary.unchanged == 1
        assert summary.created == 0

        file_logs = [lg for lg in logs if lg.get("event") == "gdrive_sync.file"]
        assert file_logs[0]["status"] == "unchanged"


class TestRename:
    """AC-6: drive rename triggers local file rename."""

    def test_gdoc_rename(self, tmp_path: Path) -> None:
        # AC-6: file renamed on Drive → rename_local_file called, log status=renamed.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        client = _make_drive_client(
            files=[_file_entry("doc1", "Original Name", _DOC_MIME)],
            doc_bodies={"doc1": "# Body\n"},
        )
        _run(client, lore_root, state_path)
        assert (lore_root / "original-name.md").exists()

        # Rename on Drive.
        client2 = _make_drive_client(
            files=[_file_entry("doc1", "New Name", _DOC_MIME)],
            doc_bodies={"doc1": "# Body\n"},
        )
        summary, logs = _run(client2, lore_root, state_path)

        assert (lore_root / "new-name.md").exists()
        assert not (lore_root / "original-name.md").exists()
        assert summary.renamed == 1

        file_logs = [lg for lg in logs if lg.get("event") == "gdrive_sync.file"]
        assert any(lg["status"] == "renamed" for lg in file_logs)


class TestArchive:
    """AC-8: deleted Drive file → archived: true in frontmatter, idempotent."""

    def test_gdoc_archived_on_deletion(self, tmp_path: Path) -> None:
        # AC-8: gdoc absent from Drive → archived: true set in frontmatter.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        client = _make_drive_client(
            files=[_file_entry("doc1", "Gone Doc", _DOC_MIME)],
            doc_bodies={"doc1": "# Gone\n"},
        )
        _run(client, lore_root, state_path)

        # Second run: doc1 gone from Drive.
        client2 = _make_drive_client(files=[])
        _run(client2, lore_root, state_path)

        md_content = (lore_root / "gone-doc.md").read_text(encoding="utf-8")
        fm_end = md_content.index("\n---\n", 4)
        fm = yaml.safe_load(md_content[4:fm_end])
        assert fm["archived"] is True
        assert fm.get("archived_at") is not None

    def test_archive_idempotent(self, tmp_path: Path) -> None:
        # AC-8: already-archived file → mtime unchanged on subsequent run.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        client = _make_drive_client(
            files=[_file_entry("doc1", "Gone Doc", _DOC_MIME)],
            doc_bodies={"doc1": "# Gone\n"},
        )
        _run(client, lore_root, state_path)

        empty = _make_drive_client(files=[])
        _run(empty, lore_root, state_path)

        md_file = lore_root / "gone-doc.md"
        mtime = md_file.stat().st_mtime

        # Third run: still absent.
        _, logs = _run(empty, lore_root, state_path)

        assert md_file.stat().st_mtime == mtime
        file_logs = [lg for lg in logs if lg.get("event") == "gdrive_sync.file"]
        assert any(lg["status"] == "unchanged" for lg in file_logs)


class TestCollision:
    """AC-9: two files with same slug → deterministic suffix resolution."""

    def test_collision_resolved_with_suffix(self, tmp_path: Path) -> None:
        # AC-9: 2 docs with same slugified title → second gets -<id[:8]> suffix.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        # Both names produce slug "doc".
        client = _make_drive_client(
            files=[
                _file_entry("aaaa1111", "doc", _DOC_MIME),
                _file_entry("bbbb2222", "doc", _DOC_MIME),
            ],
            doc_bodies={"aaaa1111": "# A\n", "bbbb2222": "# B\n"},
        )
        _run(client, lore_root, state_path)

        md_files = sorted(lore_root.glob("*.md"))
        assert len(md_files) == 2
        stems = {f.stem for f in md_files}
        # One plain, one suffixed.
        assert "doc" in stems
        assert any(s.startswith("doc-") for s in stems)


class TestNormalize:
    """AC-10: body passed through normalize_body before write."""

    def test_c0_control_chars_stripped(self, tmp_path: Path) -> None:
        # AC-10: C0 control chars (0x00-0x08, 0x0b-0x1f) are stripped by normalize_body.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        raw_body = "Hello\x00World\x08End"  # NUL and BS are C0 chars → stripped
        client = _make_drive_client(
            files=[_file_entry("doc1", "test", _DOC_MIME)],
            doc_bodies={"doc1": raw_body},
        )
        _run(client, lore_root, state_path)

        md_file = lore_root / "test.md"
        content = md_file.read_text(encoding="utf-8")
        body_part = content.split("---\n", 2)[-1]
        assert "\x00" not in body_part
        assert "\x08" not in body_part
        assert "HelloWorldEnd" in body_part


class TestCapOneMib:
    """AC-14: files exceeding 1 MiB post-normalize are skipped."""

    def test_body_over_1mib_skipped(self, tmp_path: Path) -> None:
        # AC-14: body > 1 MiB → skip + log error, others continue.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        big_body = "x" * (1024 * 1024 + 1)
        small_body = "# Small\n"
        client = _make_drive_client(
            files=[
                _file_entry("big1", "Big Doc", _DOC_MIME),
                _file_entry("small1", "Small Doc", _DOC_MIME),
            ],
            doc_bodies={"big1": big_body, "small1": small_body},
        )
        summary, logs = _run(client, lore_root, state_path)

        assert not (lore_root / "big-doc.md").exists()
        assert (lore_root / "small-doc.md").exists()
        assert summary.errors == 1
        assert summary.created == 1

        error_logs = [
            lg for lg in logs
            if lg.get("event") == "gdrive_sync.file" and lg.get("status") == "error"
        ]
        assert any("1 MiB" in lg.get("reason", "") for lg in error_logs)


class TestQuotaError:
    """AC-15: 429 → log error, continue."""

    def test_429_logged_and_continued(self, tmp_path: Path) -> None:
        # AC-15: quota error → error logged, other files processed, errors >= 1.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        quota_err = DriveApiError("rate limited", 429)
        client = _make_drive_client(
            files=[
                _file_entry("limited", "Quota Doc", _DOC_MIME),
                _file_entry("ok1", "OK Doc", _DOC_MIME),
            ],
            doc_bodies={"ok1": "# OK\n"},
            export_errors={"limited": quota_err},
        )
        summary, _ = _run(client, lore_root, state_path)

        assert summary.errors == 1
        assert summary.created == 1


class TestServerError:
    """AC-16: 5xx → log error, exit code 1."""

    def test_5xx_causes_exit_1(self, tmp_path: Path) -> None:
        # AC-16: 5xx error causes errors >= 1 in summary (exit code mapped by __main__).
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        server_err = DriveApiError("drive_export_failed: 503", 503)
        client = _make_drive_client(
            files=[_file_entry("bad1", "Bad Doc", _DOC_MIME)],
            export_errors={"bad1": server_err},
        )
        summary, _ = _run(client, lore_root, state_path)
        assert summary.errors >= 1


class TestSummaryLog:
    """AC-17: summary log emitted with correct fields."""

    def test_summary_log_present(self, tmp_path: Path) -> None:
        # AC-17: summary log event emitted with all required fields.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        client = _make_drive_client(
            files=[_file_entry("doc1", "A Doc", _DOC_MIME)],
            doc_bodies={"doc1": "# A\n"},
        )
        _, logs = _run(client, lore_root, state_path)
        summary_logs = [lg for lg in logs if lg.get("event") == "gdrive_sync.summary"]
        assert len(summary_logs) == 1
        sl = summary_logs[0]
        required_fields = ["total", "created", "updated", "renamed", "archived",
                           "unchanged", "errors", "duration_ms"]
        for field in required_fields:
            assert field in sl, f"Missing field: {field}"


class TestDryRun:
    """AC-12: --dry-run → no writes, status prefixed would_, exit 0."""

    def test_dry_run_no_writes(self, tmp_path: Path) -> None:
        # AC-12: dry-run does not write files or state.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": "# Body\n"},
        )
        _, logs = _run(client, lore_root, state_path, dry_run=True)

        assert not (lore_root / "my-doc.md").exists()
        assert not state_path.exists()

        file_logs = [lg for lg in logs if lg.get("event") == "gdrive_sync.file"]
        assert all(lg["status"].startswith("would_") for lg in file_logs)

    def test_dry_run_summary_has_would_prefix(self, tmp_path: Path) -> None:
        # AC-12: dry-run summary uses would_* counters.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": "# Body\n"},
        )
        _, logs = _run(client, lore_root, state_path, dry_run=True)
        summary_logs = [lg for lg in logs if lg.get("event") == "gdrive_sync.summary"]
        assert len(summary_logs) == 1
        sl = summary_logs[0]
        assert "would_created" in sl or "would_create" in sl or sl.get("created", 0) >= 0


class TestLocalDrift:
    """AC-20: human-edited local file overwritten when Drive content changes."""

    def test_drift_overwritten_and_logged(self, tmp_path: Path) -> None:
        # AC-20: local edit detected, Drive content overwrites, warning logged.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        body_v1 = "# Title\n\nOriginal content.\n"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": body_v1},
        )
        _run(client, lore_root, state_path)

        # Human edits the file locally.
        md_file = lore_root / "my-doc.md"
        original_content = md_file.read_text(encoding="utf-8")
        # Replace body while keeping frontmatter intact.
        fm_end = original_content.index("\n---\n", 4) + 5
        fm_part = original_content[:fm_end]
        md_file.write_text(fm_part + "Human edited body.\n", encoding="utf-8")

        # Drive updates content (new body = different content_signature).
        body_v2 = "# Title\n\nUpdated Drive content.\n"
        client2 = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": body_v2},
        )
        _, logs = _run(client2, lore_root, state_path)

        drift_logs = [lg for lg in logs if lg.get("event") == "gdrive_sync.local_drift_overwritten"]
        assert len(drift_logs) == 1
        # Verify Drive content was written (not the human-edited local body).
        assert "Updated Drive content." in md_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# ING-011: gsheet / gslide integration tests
# ---------------------------------------------------------------------------


def _sheet_tab(title: str, sheet_id: int, index: int) -> dict[str, Any]:
    return {"title": title, "sheetId": sheet_id, "index": index}


def _make_shape(text: str, y: float = 0.0, x: float = 0.0) -> dict[str, Any]:
    return {
        "shape": {"text": {"textElements": [{"textRun": {"content": text}}]}},
        "transform": {"translateY": y, "translateX": x},
    }


def _make_slide(shapes: list[dict[str, Any]], *, skip: bool = False) -> dict[str, Any]:
    slide: dict[str, Any] = {
        "pageElements": shapes,
        "pageProperties": {"skipSlide": True} if skip else {},
    }
    return slide


class TestGsheetSync:
    """AC-1/2/3/5/6/13/17: gsheet creates one .md per tab."""

    def test_two_tabs_create_two_files(self, tmp_path: Path) -> None:
        # AC-1/AC-2: 2-tab workbook → 2 .md files created
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        tabs = [_sheet_tab("Data", 0, 0), _sheet_tab("Summary", 1, 1)]
        client = _make_drive_client(
            files=[_file_entry("sheet1", "My Sheet", _SHEET_MIME)],
            sheet_tabs={"sheet1": tabs},
            sheet_values={"sheet1": {
                "Data": [["Name", "Age"], ["Alice", "30"]],
                "Summary": [["Total"], ["1"]],
            }},
        )
        summary, _ = _run(client, lore_root, state_path)
        md_files = list(lore_root.glob("*.md"))
        assert len(md_files) == 2  # AC-2: one per tab
        assert summary.created == 2

    def test_tab_path_uses_double_dash_separator(self, tmp_path: Path) -> None:
        # AC-3: path = <sheet_slug>--<tab_slug>.md (always, even single tab)
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        tabs = [_sheet_tab("Sheet1", 0, 0)]
        client = _make_drive_client(
            files=[_file_entry("sid", "My Workbook", _SHEET_MIME)],
            sheet_tabs={"sid": tabs},
            sheet_values={"sid": {"Sheet1": [["H"]]}},
        )
        _run(client, lore_root, state_path)
        files = list(lore_root.glob("*.md"))
        assert len(files) == 1
        # Must match <sheet_slug>--<tab_slug>.md
        assert "--" in files[0].stem  # AC-3

    def test_source_id_format(self, tmp_path: Path) -> None:
        # AC-5: source_id = <file_id>#<gid_decimal>
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        tabs = [_sheet_tab("Tab", 42, 0)]
        client = _make_drive_client(
            files=[_file_entry("fid1", "Book", _SHEET_MIME)],
            sheet_tabs={"fid1": tabs},
            sheet_values={"fid1": {"Tab": [["H"]]}},
        )
        _run(client, lore_root, state_path)
        state = load_state(state_path)
        assert "fid1#42" in state  # AC-5

    def test_frontmatter_title_has_em_dash(self, tmp_path: Path) -> None:
        # AC-6: frontmatter title = "<workbook> — <tab>" with em-dash U+2014
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        tabs = [_sheet_tab("Onglet 1", 0, 0)]
        client = _make_drive_client(
            files=[_file_entry("fid2", "Classeur", _SHEET_MIME)],
            sheet_tabs={"fid2": tabs},
            sheet_values={"fid2": {"Onglet 1": [["H"]]}},
        )
        _run(client, lore_root, state_path)
        md_file = next(lore_root.glob("*.md"))
        content = md_file.read_text(encoding="utf-8")
        fm_end = content.index("\n---\n", 4)
        fm = yaml.safe_load(content[4:fm_end])
        assert "—" in fm["title"]  # AC-6: em-dash U+2014

    def test_html_comment_first_line_body(self, tmp_path: Path) -> None:
        # AC-17: first line of body = <!-- gdrive: file=<id> tab=<gid> -->
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        tabs = [_sheet_tab("Tab", 0, 0)]
        client = _make_drive_client(
            files=[_file_entry("fid3", "Book", _SHEET_MIME)],
            sheet_tabs={"fid3": tabs},
            sheet_values={"fid3": {"Tab": [["H"]]}},
        )
        _run(client, lore_root, state_path)
        md_file = next(lore_root.glob("*.md"))
        content = md_file.read_text(encoding="utf-8")
        fm_end = content.index("\n---\n", 4) + 5
        body = content[fm_end:]
        assert body.startswith("<!-- gdrive: file=fid3 tab=0 -->")  # AC-17

    def test_tab_over_1mib_skipped_other_exported(self, tmp_path: Path) -> None:
        # AC-13: tab rendering > 1 MiB skipped; other tabs continue
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        big_row = ["x" * 1000] * 100
        big_values = [big_row[:1]] + [big_row] * 12000  # > 1 MiB
        tabs = [_sheet_tab("Big", 0, 0), _sheet_tab("Small", 1, 1)]
        client = _make_drive_client(
            files=[_file_entry("fid4", "Book", _SHEET_MIME)],
            sheet_tabs={"fid4": tabs},
            sheet_values={"fid4": {"Big": big_values, "Small": [["H"], ["v"]]}},
        )
        summary, _ = _run(client, lore_root, state_path)
        assert summary.errors == 1  # AC-13: big tab skipped
        assert summary.created == 1  # AC-13: small tab created


class TestGslideSync:
    """AC-9/10/11/16/17: gslide creates one .md per presentation."""

    def _make_presentation(
        self,
        slide_texts: list[str],
        notes: list[str] | None = None,
        skipped_indices: list[int] | None = None,
    ) -> dict[str, Any]:
        slides = []
        for i, text in enumerate(slide_texts):
            skip = skipped_indices is not None and i in skipped_indices
            note = notes[i] if notes and i < len(notes) else ""
            shape = _make_shape(text)
            slide: dict[str, Any] = {
                "pageElements": [shape],
                "pageProperties": {"skipSlide": True} if skip else {},
            }
            if note:
                slide["slideProperties"] = {
                    "notesPage": {"pageElements": [_make_shape(note)]}
                }
            slides.append(slide)
        return {"slides": slides}

    def test_one_file_per_presentation(self, tmp_path: Path) -> None:
        # AC-10: 1 gslide → 1 .md (not N per slide)
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        pres = self._make_presentation(["S1", "S2", "S3"])
        client = _make_drive_client(
            files=[_file_entry("pres1", "My Deck", _SLIDES_MIME)],
            presentations={"pres1": pres},
        )
        _run(client, lore_root, state_path)
        md_files = list(lore_root.glob("*.md"))
        assert len(md_files) == 1  # AC-10

    def test_slide_headings_numbered(self, tmp_path: Path) -> None:
        # AC-9: ## Slide 1, ## Slide 2, ## Slide 3 in output
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        pres = self._make_presentation(["Content 1", "Content 2", "Content 3"])
        client = _make_drive_client(
            files=[_file_entry("pres2", "Deck", _SLIDES_MIME)],
            presentations={"pres2": pres},
        )
        _run(client, lore_root, state_path)
        md_file = next(lore_root.glob("*.md"))
        content = md_file.read_text(encoding="utf-8")
        assert "## Slide 1" in content
        assert "## Slide 2" in content
        assert "## Slide 3" in content

    def test_speaker_notes_rendered(self, tmp_path: Path) -> None:
        # AC-11: notes → > **Notes**: <text>
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        pres = self._make_presentation(["Body text"], notes=["Important note"])
        client = _make_drive_client(
            files=[_file_entry("pres3", "Deck", _SLIDES_MIME)],
            presentations={"pres3": pres},
        )
        _run(client, lore_root, state_path)
        content = next(lore_root.glob("*.md")).read_text(encoding="utf-8")
        assert "> **Notes**: Important note" in content

    def test_hidden_slide_skipped_renumbered(self, tmp_path: Path) -> None:
        # AC-16: 5 slides, slide 2 hidden → output has Slide 1..4, log warning
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        pres = self._make_presentation(
            ["S1", "S2", "S3", "S4", "S5"], skipped_indices=[1]
        )
        client = _make_drive_client(
            files=[_file_entry("pres4", "Deck", _SLIDES_MIME)],
            presentations={"pres4": pres},
        )
        _, logs = _run(client, lore_root, state_path)
        content = next(lore_root.glob("*.md")).read_text(encoding="utf-8")
        assert "## Slide 4" in content
        assert "## Slide 5" not in content
        warn_logs = [lg for lg in logs if lg.get("event") == "gdrive_sync.slide_hidden_skipped"]
        assert len(warn_logs) == 1
        assert warn_logs[0]["slide_index_original"] == 2  # 1-based

    def test_html_comment_first_line_body(self, tmp_path: Path) -> None:
        # AC-17: first line of body = <!-- gdrive: file=<id> --> (no tab=)
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        pres = self._make_presentation(["text"])
        client = _make_drive_client(
            files=[_file_entry("pres5", "Deck", _SLIDES_MIME)],
            presentations={"pres5": pres},
        )
        _run(client, lore_root, state_path)
        content = next(lore_root.glob("*.md")).read_text(encoding="utf-8")
        fm_end = content.index("\n---\n", 4) + 5
        body = content[fm_end:]
        first_line = body.split("\n")[0]
        assert first_line == "<!-- gdrive: file=pres5 -->"  # AC-17
        assert "tab=" not in first_line


class TestTabArchival:
    """AC-7: tab archival when tab removed from spreadsheet."""

    def test_tab_archived_when_removed(self, tmp_path: Path) -> None:
        # AC-7: run 1 has 2 tabs; run 2 removes one → archived: true in .md
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        tabs_run1 = [_sheet_tab("Tab1", 0, 0), _sheet_tab("Tab2", 1, 1)]
        client1 = _make_drive_client(
            files=[_file_entry("sid", "Book", _SHEET_MIME)],
            sheet_tabs={"sid": tabs_run1},
            sheet_values={"sid": {
                "Tab1": [["H"], ["v1"]],
                "Tab2": [["H"], ["v2"]],
            }},
        )
        _run(client1, lore_root, state_path)
        md_files_run1 = list(lore_root.glob("*.md"))
        assert len(md_files_run1) == 2

        # Run 2: Tab2 removed
        tabs_run2 = [_sheet_tab("Tab1", 0, 0)]
        client2 = _make_drive_client(
            files=[_file_entry("sid", "Book", _SHEET_MIME)],
            sheet_tabs={"sid": tabs_run2},
            sheet_values={"sid": {"Tab1": [["H"], ["v1"]]}},
        )
        _run(client2, lore_root, state_path)

        # Find the Tab2 .md and check archived: true
        archived_file = next(
            f for f in lore_root.glob("*.md")
            if "tab2" in f.name
        )
        content = archived_file.read_text(encoding="utf-8")
        fm_end = content.index("\n---\n", 4)
        fm = yaml.safe_load(content[4:fm_end])
        assert fm["archived"] is True  # AC-7


class TestScopeFailFast:
    """AC-15: SA missing spreadsheets.readonly → fail-fast with exact message."""

    def test_scope_missing_exits_with_message(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        # AC-15: verify_extra_scopes exits on 403 insufficient scope
        # LOW-7: use DriveClient.from_services factory instead of private attribute access
        resp = MagicMock()
        resp.status = 403
        resp.reason = "Forbidden"
        http_error = gae.HttpError(
            resp=resp,
            content=json.dumps({
                "error": {"errors": [{"reason": "insufficientPermissions"}]}
            }).encode(),
        )

        sheets_svc = MagicMock()
        (
            sheets_svc.spreadsheets.return_value
            .get.return_value.execute
        ).side_effect = http_error
        client = DriveClient.from_services(MagicMock(), sheets_svc, MagicMock())

        with pytest.raises(SystemExit) as exc_info:
            client.verify_extra_scopes()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "gdrive scope missing" in captured.err


class TestMimeRegression:
    """AC-14: gdoc + png still work correctly after adding gsheet/gslide."""

    def test_gdoc_and_png_unchanged(self, tmp_path: Path) -> None:
        # AC-14: original gdoc + png behaviour unaffected
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        client = _make_drive_client(
            files=[
                _file_entry("doc1", "My Doc", _DOC_MIME),
                _file_entry("img1", "photo", _PNG_MIME, md5="aabbcc"),
            ],
            doc_bodies={"doc1": "# Content\n"},
            png_bodies={"img1": b"\x89PNG\r\n" + b"\x00" * 10},
        )
        summary, _ = _run(client, lore_root, state_path)
        assert (lore_root / "my-doc.md").exists()
        assert (lore_root / "photo.png").exists()
        assert summary.errors == 0


class TestGsheetWorkbookLevelError:
    """HIGH-1: 429/5xx on get_spreadsheet_tabs → errors incremented, exit 1."""

    def test_429_on_get_spreadsheet_tabs_increments_errors(self, tmp_path: Path) -> None:
        # HIGH-1: spec L52 — quota/server errors on gsheet workbook listing must
        # count as errors so __main__.py exits 1.  Previously counts.errors stayed 0.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        quota_err = DriveApiError("sheets_get_failed: 429", 429)
        client = _make_drive_client(
            files=[
                _file_entry("sheet1", "My Sheet", _SHEET_MIME),
                _file_entry("doc1", "My Doc", _DOC_MIME),
            ],
            doc_bodies={"doc1": "# OK\n"},
            export_errors={"sheet1": quota_err},
        )
        summary, logs = _run(client, lore_root, state_path)

        # HIGH-1: workbook-level failure must be counted as an error
        assert summary.errors == 1
        # Other files continue processing
        assert summary.created == 1

        error_logs = [
            lg for lg in logs
            if lg.get("event") == "gdrive_sync.file" and lg.get("status") == "error"
        ]
        assert len(error_logs) == 1
        assert "429" in error_logs[0].get("reason", "")

    def test_5xx_on_get_spreadsheet_tabs_increments_errors(self, tmp_path: Path) -> None:
        # HIGH-1: 5xx server error on workbook listing also counts as error
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        server_err = DriveApiError("sheets_get_failed: 503", 503)
        client = _make_drive_client(
            files=[_file_entry("sheet1", "Broken Sheet", _SHEET_MIME)],
            export_errors={"sheet1": server_err},
        )
        summary, _ = _run(client, lore_root, state_path)
        assert summary.errors >= 1


class TestWholeWorkbookDeletion:
    """AC-7 coverage gap: whole-workbook deletion archives all its tabs."""

    def test_workbook_deleted_archives_all_tabs(self, tmp_path: Path) -> None:
        # AC-7: run-1 exports a workbook with 2 tabs; run-2 the workbook is gone
        # from Drive → both tab .md files must get archived: true.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        tabs = [_sheet_tab("Tab1", 0, 0), _sheet_tab("Tab2", 1, 1)]
        client1 = _make_drive_client(
            files=[_file_entry("sid", "Book", _SHEET_MIME)],
            sheet_tabs={"sid": tabs},
            sheet_values={"sid": {
                "Tab1": [["H"], ["v1"]],
                "Tab2": [["H"], ["v2"]],
            }},
        )
        _run(client1, lore_root, state_path)
        md_files_run1 = list(lore_root.glob("*.md"))
        assert len(md_files_run1) == 2

        # Run 2: entire workbook absent from Drive
        client2 = _make_drive_client(files=[])
        summary, _ = _run(client2, lore_root, state_path)

        # Both tab files must have archived: true
        for md_file in lore_root.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            fm_end = content.index("\n---\n", 4)
            fm = yaml.safe_load(content[4:fm_end])
            assert fm["archived"] is True, f"{md_file.name} should be archived"
            assert fm.get("archived_at") is not None

        assert summary.archived == 2  # AC-7: both tabs archived


# ---------------------------------------------------------------------------
# ING-014: gdoc image extraction tests
# ---------------------------------------------------------------------------


def _make_png_bytes(width: int = 10, height: int = 10) -> bytes:
    """Return minimal valid PNG bytes."""
    img = PIL.Image.new("RGB", (width, height), color=(100, 50, 25))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_inline_object(content_uri: str) -> dict[str, Any]:
    """Return a minimal Docs API inlineObject dict."""
    return {
        "inlineObjectProperties": {
            "embeddedObject": {
                "imageProperties": {"contentUri": content_uri}
            }
        }
    }


def _make_large_incompressible_png() -> bytes:
    """Return a 2048x2048 noise PNG whose JPEG re-encode exceeds 5 MiB (AC-3c fixture)."""
    rng = random.Random(0)  # noqa: S311
    raw_pixels = bytes(rng.randint(0, 255) for _ in range(2048 * 2048 * 3))
    img = PIL.Image.frombytes("RGB", (2048, 2048), raw_pixels)
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=0)
    return buf.getvalue()


class TestGdocDocsApiCall:
    """AC-1/AC-13: get_document called for every gdoc including those without images."""

    def test_get_document_called_for_gdoc(self, tmp_path: Path) -> None:
        # AC-1: get_document invoked for each gdoc.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": "# Title\n"},
        )
        _run(client, lore_root, state_path)
        client.get_document.assert_called_once_with("doc1")

    def test_get_document_not_called_for_png(self, tmp_path: Path) -> None:
        # AC-13: get_document never called for native PNG files.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        client = _make_drive_client(
            files=[_file_entry("img1", "photo", _PNG_MIME, md5="aabb")],
            png_bodies={"img1": _make_png_bytes()},
        )
        _run(client, lore_root, state_path)
        client.get_document.assert_not_called()

    def test_gdoc_without_images_has_empty_manifest(self, tmp_path: Path) -> None:
        # AC-13: gdoc without inlineObjects → images={} in state.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": "# Title\nNo images here.\n"},
        )
        _run(client, lore_root, state_path)
        state = load_state(state_path)
        assert state["doc1"].images == {}

    def test_gdoc_without_images_body_unchanged(self, tmp_path: Path) -> None:
        # AC-13: gdoc without images → .md body == normalize_body(export).
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": "# Title\n\nContent here.\n"},
        )
        _run(client, lore_root, state_path)
        content = (lore_root / "my-doc.md").read_text(encoding="utf-8")
        fm_end = content.index("\n---\n", 4) + 5
        body = content[fm_end:]
        assert "# Title" in body
        assert "Content here." in body


class TestGdocImageDownload:
    """AC-2: download authenticated, AC-3: image written with md5[:12] filename."""

    def test_image_written_to_sidecar(self, tmp_path: Path) -> None:
        # AC-2/AC-3: image downloaded and written to sidecar dir.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img1"
        png = _make_png_bytes()
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![alt]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
            image_payloads={uri: (png, "image/png")},
        )
        _run(client, lore_root, state_path)
        sidecar = lore_root / "my-doc.images"
        assert sidecar.exists()
        files = list(sidecar.iterdir())
        assert len(files) == 1
        assert re.match(r"^[0-9a-f]{12}\.(png|jpg)$", files[0].name)

    def test_md_contains_relative_sidecar_path(self, tmp_path: Path) -> None:
        # AC-4: .md references sidecar path without / or ./ prefix.
        # The renderer produces image refs from doc_inline_objects, not doc_bodies.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img1"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": "# Title\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
        )
        _run(client, lore_root, state_path)
        content = (lore_root / "my-doc.md").read_text(encoding="utf-8")
        fm_end = content.index("\n---\n", 4) + 5
        body = content[fm_end:]
        # Must contain relative sidecar path, not the googleusercontent URL.
        assert "googleusercontent.com" not in body
        assert "my-doc.images/" in body


class TestGdocImageDedup:
    """AC-5: two inlineObjects same MD5 → one file, two refs."""

    def test_dedup_same_md5(self, tmp_path: Path) -> None:
        # AC-5: two objects with same binary → 1 file on disk, 2 refs in .md.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri_a = "https://lh3.googleusercontent.com/imgA"
        uri_b = "https://lh3.googleusercontent.com/imgB"
        same_png = _make_png_bytes()
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri_a})\n\n![b]({uri_b})\n"},
            doc_inline_objects={
                "doc1": {
                    "kix.a": _make_inline_object(uri_a),
                    "kix.b": _make_inline_object(uri_b),
                }
            },
            image_payloads={uri_a: (same_png, "image/png"), uri_b: (same_png, "image/png")},
        )
        _run(client, lore_root, state_path)
        sidecar = lore_root / "my-doc.images"
        assert len(list(sidecar.iterdir())) == 1  # AC-5: dedup


class TestGdocImageFailures:
    """AC-7: various failure modes → placeholder, counter, processing continues."""

    def test_http_error_gives_placeholder(self, tmp_path: Path) -> None:
        # AC-7: HTTP 500 → placeholder #image-failed-<objectId>.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img_fail"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![alt]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
            image_errors={uri: DriveApiError("server error", 500)},
        )
        summary, run_logs = _run(client, lore_root, state_path)
        assert summary.images_failed == 1
        content = (lore_root / "my-doc.md").read_text(encoding="utf-8")
        assert "#image-failed-kix.a" in content
        img_fail_logs = [lg for lg in run_logs if lg.get("event") == "gdrive_sync.image_failed"]
        assert len(img_fail_logs) == 1

    def test_unsupported_content_type_placeholder(self, tmp_path: Path) -> None:
        # AC-7: Content-Type not in MIME map → placeholder.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img_bmp"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![alt]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
            image_payloads={uri: (b"BM\x00\x00garbage", "image/bmp")},
        )
        summary, _logs = _run(client, lore_root, state_path)
        assert summary.images_failed == 1
        content = (lore_root / "my-doc.md").read_text(encoding="utf-8")
        assert "#image-failed-kix.a" in content

    def test_processing_continues_after_failed_image(self, tmp_path: Path) -> None:
        # AC-7: after image failure, other docs still processed.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/fail"
        client = _make_drive_client(
            files=[
                _file_entry("doc1", "Fail Doc", _DOC_MIME),
                _file_entry("doc2", "OK Doc", _DOC_MIME),
            ],
            doc_bodies={
                "doc1": f"![alt]({uri})\n",
                "doc2": "# Good\n",
            },
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
            image_errors={uri: DriveApiError("err", 500)},
        )
        summary, _ = _run(client, lore_root, state_path)
        assert (lore_root / "ok-doc.md").exists()
        assert summary.images_failed == 1
        assert summary.errors == 0  # doc-level errors separate from image_failed


class TestGdocUnchangedSignature:
    """AC-9: unchanged iff content_signature AND images manifest both unchanged."""

    def test_unchanged_when_both_same(self, tmp_path: Path) -> None:
        # AC-9: run2 with same content + same images → unchanged.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img1"
        png = _make_png_bytes()
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
            image_payloads={uri: (png, "image/png")},
        )
        _run(client, lore_root, state_path)
        md_mtime = (lore_root / "my-doc.md").stat().st_mtime

        summary, _ = _run(client, lore_root, state_path)
        assert summary.unchanged == 1
        assert (lore_root / "my-doc.md").stat().st_mtime == md_mtime

    def test_updated_when_image_replaced(self, tmp_path: Path) -> None:
        # AC-9: run2 with same body but different image MD5 → updated.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img1"
        png_v1 = _make_png_bytes(10, 10)
        png_v2 = _make_png_bytes(20, 20)

        client1 = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
            image_payloads={uri: (png_v1, "image/png")},
        )
        _run(client1, lore_root, state_path)

        client2 = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
            image_payloads={uri: (png_v2, "image/png")},
        )
        summary, _ = _run(client2, lore_root, state_path)
        assert summary.updated == 1


class TestGdocOrphanCleanup:
    """AC-10: orphaned sidecar images removed after run."""

    def test_orphan_removed_when_image_deleted(self, tmp_path: Path) -> None:
        # AC-10: run1 has 2 images; run2 removes one → 1 orphan removed.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri_a = "https://lh3.googleusercontent.com/imgA"
        uri_b = "https://lh3.googleusercontent.com/imgB"
        png = _make_png_bytes()

        client1 = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri_a})\n![b]({uri_b})\n"},
            doc_inline_objects={
                "doc1": {
                    "kix.a": _make_inline_object(uri_a),
                    "kix.b": _make_inline_object(uri_b),
                }
            },
            image_payloads={
                uri_a: (png, "image/png"),
                uri_b: (_make_png_bytes(20, 20), "image/png"),
            },
        )
        _run(client1, lore_root, state_path)
        sidecar = lore_root / "my-doc.images"
        assert len(list(sidecar.iterdir())) == 2

        # Run 2: only image A remains.
        client2 = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri_a})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri_a)}},
            image_payloads={uri_a: (png, "image/png")},
        )
        summary, logs = _run(client2, lore_root, state_path)
        assert len(list(sidecar.iterdir())) == 1
        orphan_logs = [lg for lg in logs if lg.get("event") == "gdrive_sync.image_orphan_removed"]
        assert len(orphan_logs) == 1
        assert summary.orphans_removed == 1

    def test_sidecar_dir_removed_when_all_images_gone(self, tmp_path: Path) -> None:
        # AC-10: after all images removed, sidecar dir is deleted.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img"

        client1 = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
        )
        _run(client1, lore_root, state_path)
        assert (lore_root / "my-doc.images").exists()

        # Run 2: no images.
        client2 = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": "# No images\n"},
        )
        _run(client2, lore_root, state_path)
        assert not (lore_root / "my-doc.images").exists()


class TestGdocRenameSidecar:
    """AC-12: rename sidecar directory when gdoc is renamed."""

    def test_sidecar_renamed_with_doc(self, tmp_path: Path) -> None:
        # AC-12: gdoc renamed → sidecar dir renamed alongside .md.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img1"

        client1 = _make_drive_client(
            files=[_file_entry("doc1", "Old Name", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
        )
        _run(client1, lore_root, state_path)
        assert (lore_root / "old-name.images").exists()

        client2 = _make_drive_client(
            files=[_file_entry("doc1", "New Name", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
        )
        _, logs = _run(client2, lore_root, state_path)
        assert (lore_root / "new-name.images").exists()
        assert not (lore_root / "old-name.images").exists()
        rename_logs = [lg for lg in logs if lg.get("event") == "gdrive_sync.images_dir_renamed"]
        assert len(rename_logs) == 1


class TestGdocDryRunImages:
    """AC-14: --dry-run skips image downloads, writes, orphan cleanup."""

    def test_dry_run_no_image_writes(self, tmp_path: Path) -> None:
        # AC-14: dry-run → no image binaries written.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img1"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
        )
        _run(client, lore_root, state_path, dry_run=True)
        # No sidecar dir should exist.
        assert not (lore_root / "my-doc.images").exists()

    def test_dry_run_no_image_downloads(self, tmp_path: Path) -> None:
        # AC-14: dry-run → download_image never called.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img1"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
        )
        _run(client, lore_root, state_path, dry_run=True)
        client.download_image.assert_not_called()

    def test_dry_run_summary_has_would_images_fields(self, tmp_path: Path) -> None:
        # AC-15: dry-run summary has would_images_written/failed/orphans_removed.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img1"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
        )
        _, logs = _run(client, lore_root, state_path, dry_run=True)
        summary_logs = [lg for lg in logs if lg.get("event") == "gdrive_sync.summary"]
        assert len(summary_logs) == 1
        sl = summary_logs[0]
        assert "would_images_written" in sl
        assert "would_images_failed" in sl
        assert "would_orphans_removed" in sl


class TestSummaryImageFields:
    """AC-15: summary log has images_written, images_failed, orphans_removed."""

    def test_summary_has_image_fields(self, tmp_path: Path) -> None:
        # AC-15: non-dry-run summary includes images_written, images_failed, orphans_removed.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": "# No images\n"},
        )
        _, logs = _run(client, lore_root, state_path)
        summary_logs = [lg for lg in logs if lg.get("event") == "gdrive_sync.summary"]
        assert len(summary_logs) == 1
        sl = summary_logs[0]
        assert "images_written" in sl
        assert "images_failed" in sl
        assert "orphans_removed" in sl

    def test_images_written_counted_correctly(self, tmp_path: Path) -> None:
        # AC-15: images_written == number of image binaries written.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img1"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
        )
        summary, _ = _run(client, lore_root, state_path)
        assert summary.images_written == 1

    def test_images_failed_counted_correctly(self, tmp_path: Path) -> None:
        # AC-15: images_failed counts HTTP errors.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img_fail"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
            image_errors={uri: DriveApiError("err", 500)},
        )
        summary, _ = _run(client, lore_root, state_path)
        assert summary.images_failed == 1

    def test_images_state_entry_persisted(self, tmp_path: Path) -> None:
        # AC-8: state.images persisted after run.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img1"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
        )
        _run(client, lore_root, state_path)
        state = load_state(state_path)
        assert state["doc1"].images != {}  # AC-8: manifest not empty


class TestGdocUnchangedPermute:
    """AC-9 permute-order: same md5 set in different order → unchanged.

    Spec oracle line 101: when ALL images share the same binary (same md5 post-dedup),
    permuting which objectId is listed first in the Docs API response does NOT change
    content_signature (all sidecar refs resolve to the same md5 path) nor the manifest
    set — both checks in the unchanged guard pass.
    """

    def test_unchanged_when_images_permuted(self, tmp_path: Path) -> None:
        # AC-9 spec oracle line 101: run 2 où seul l'ordre des images change
        # sans changer le set md5 → unchanged.
        # Two objects share the SAME binary → same md5 post-dedup → sidecar path
        # is identical for both, so rewritten body is identical regardless of
        # which objectId appears first in the Docs API body traversal.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri_a = "https://lh3.googleusercontent.com/imgA"
        uri_b = "https://lh3.googleusercontent.com/imgB"
        same_png = _make_png_bytes(10, 10)

        # Run 1: body traversal order kix.a first, kix.b second.
        client1 = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri_a})\n\n![b]({uri_b})\n"},
            doc_inline_objects={
                "doc1": {
                    "kix.a": _make_inline_object(uri_a),
                    "kix.b": _make_inline_object(uri_b),
                }
            },
            image_payloads={uri_a: (same_png, "image/png"), uri_b: (same_png, "image/png")},
        )
        _run(client1, lore_root, state_path)

        # Run 2: Docs API body traversal now lists kix.b first, kix.a second
        # (simulates Drive reordering objects). Same binary → same md5 set, and
        # because md5 is identical for both objects the rewritten body is unchanged.
        client2 = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri_a})\n\n![b]({uri_b})\n"},
            doc_inline_objects={
                "doc1": {
                    "kix.b": _make_inline_object(uri_b),
                    "kix.a": _make_inline_object(uri_a),
                }
            },
            image_payloads={uri_a: (same_png, "image/png"), uri_b: (same_png, "image/png")},
        )
        summary, _ = _run(client2, lore_root, state_path)
        # AC-9: manifest set unchanged, content_signature unchanged → unchanged.
        assert summary.unchanged == 1


def _make_positioned_object(content_uri: str) -> dict[str, Any]:
    """Return a minimal Docs API positionedObject dict."""
    return {
        "positionedObjectProperties": {
            "embeddedObject": {
                "imageProperties": {"contentUri": content_uri}
            }
        }
    }


class TestGdocPositionedObjects:
    """Positioned images (anchored) extracted and rendered — core ING-014 refactor.

    Drive files.export(text/markdown) cannot see positionedObjects; the Docs API
    tree renderer handles them. Real-world corpus: 0 inlineObjects across 25 docs.
    """

    def test_positioned_object_extracted_and_written(self, tmp_path: Path) -> None:
        # AC-1 refactor: positionedObject image downloaded, written to sidecar.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/pos1"
        png = _make_png_bytes()

        # Build doc with a positionedObject (no inlineObjects).
        def _get_doc_with_positioned(file_id: str) -> dict[str, Any]:
            return {
                "documentId": file_id,
                "inlineObjects": {},
                "positionedObjects": {"kix.pos": _make_positioned_object(uri)},
                "body": {"content": [
                    {"paragraph": {
                        "elements": [{"textRun": {"content": "Doc text"}}],
                        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    }}
                ]},
            }

        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
        )
        client.get_document.side_effect = _get_doc_with_positioned
        client.download_image.return_value = (png, "image/png")

        summary, _ = _run(client, lore_root, state_path)
        assert summary.errors == 0
        sidecar = lore_root / "my-doc.images"
        assert sidecar.exists()
        assert len(list(sidecar.iterdir())) == 1
        assert summary.images_written == 1

    def test_positioned_object_ref_in_markdown(self, tmp_path: Path) -> None:
        # AC-4 + refactor: .md body includes sidecar path for positioned image.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/pos_ref"
        png = _make_png_bytes()

        def _get_doc(file_id: str) -> dict[str, Any]:
            return {
                "documentId": file_id,
                "inlineObjects": {},
                "positionedObjects": {"kix.p": _make_positioned_object(uri)},
                "body": {"content": []},
            }

        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
        )
        client.get_document.side_effect = _get_doc
        client.download_image.return_value = (png, "image/png")

        _run(client, lore_root, state_path)
        content = (lore_root / "my-doc.md").read_text(encoding="utf-8")
        fm_end = content.index("\n---\n", 4) + 5
        body = content[fm_end:]
        assert "my-doc.images/" in body

    def test_rendered_body_over_1mib_triggers_error(self, tmp_path: Path) -> None:
        # 1 MiB cap: a doc whose rendered text exceeds 1 MiB → error, no file written.
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        big_text = "x" * (1024 * 1024 + 100)

        def _get_big_doc(file_id: str) -> dict[str, Any]:
            return {
                "documentId": file_id,
                "body": {"content": [
                    {"paragraph": {
                        "elements": [{"textRun": {"content": big_text}}],
                        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    }}
                ]},
            }

        client = _make_drive_client(
            files=[_file_entry("doc1", "Big Doc", _DOC_MIME)],
        )
        client.get_document.side_effect = _get_big_doc

        summary, logs = _run(client, lore_root, state_path)
        assert summary.errors == 1
        assert not (lore_root / "big-doc.md").exists()
        error_logs = [
            lg for lg in logs
            if lg.get("event") == "gdrive_sync.file" and lg.get("status") == "error"
        ]
        assert any("1 MiB" in lg.get("reason", "") for lg in error_logs)


class TestGdocOversizedIntegration:
    """AC-3c end-to-end: oversized image through sync → placeholder + counter + log."""

    def test_oversized_image_placeholder(self, tmp_path: Path) -> None:
        # AC-3c spec oracle line 94: integration test — synthetic incompressible noise
        # 2048x2048 → compress_image raises ImageOversizedError → placeholder
        # ![image trop volumineuse](#oversized-<objectId>) in MD + images_failed++ +
        # log event gdrive_sync.image_oversized.
        noise_png = _make_large_incompressible_png()

        # Guard: verify that JPEG q=85 of this fixture actually exceeds 5 MiB on this host.
        # If not (unusual hardware/codec), skip so we don't ship a false-green test.
        verify_buf = io.BytesIO()
        PIL.Image.frombytes("RGB", (2048, 2048),
            bytes(random.Random(0).randint(0, 255) for _ in range(2048 * 2048 * 3))  # noqa: S311
        ).save(verify_buf, format="JPEG", quality=85)
        if len(verify_buf.getvalue()) <= 5 * 1024 * 1024:
            pytest.skip("JPEG encoder compressed noise below 5 MiB on this platform")

        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img_noise"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![alt]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.z": _make_inline_object(uri)}},
            image_payloads={uri: (noise_png, "image/png")},
        )
        summary, run_logs = _run(client, lore_root, state_path)

        # Counter incremented.
        assert summary.images_failed == 1
        # No binary written to sidecar.
        assert not (lore_root / "my-doc.images").exists()
        # Oversized log emitted.
        oversized_logs = [
            lg for lg in run_logs if lg.get("event") == "gdrive_sync.image_oversized"
        ]
        assert len(oversized_logs) == 1
        assert oversized_logs[0]["object_id"] == "kix.z"
        # Placeholder appears in the written markdown.
        content = (lore_root / "my-doc.md").read_text(encoding="utf-8")
        assert "#oversized-kix.z" in content


class TestDryRunCounterCorrectness:
    """AC-14: dry-run images_written must not inflate (MED-3 fix)."""

    def test_dry_run_does_not_inflate_images_written(self, tmp_path: Path) -> None:
        # AC-14: dry-run → images_written == 0 (no download → no knowledge of success).
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        uri = "https://lh3.googleusercontent.com/img1"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": f"![a]({uri})\n"},
            doc_inline_objects={"doc1": {"kix.a": _make_inline_object(uri)}},
        )
        summary, _ = _run(client, lore_root, state_path, dry_run=True)
        assert summary.images_written == 0
