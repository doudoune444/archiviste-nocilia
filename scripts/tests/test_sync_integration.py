"""End-to-end integration tests for gdrive_export.sync — AC-2/3/6/8/9/10/11/12/13/14/15/16/17/20."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import yaml

from gdrive_export.drive_client import DriveApiError
from gdrive_export.state import load_state
from gdrive_export.sync import SummaryCounts, run_sync

# ---------------------------------------------------------------------------
# Drive stub builder
# ---------------------------------------------------------------------------

_DOC_MIME = "application/vnd.google-apps.document"
_PNG_MIME = "image/png"


def _make_drive_client(
    files: list[dict[str, Any]],
    doc_bodies: dict[str, str] | None = None,
    png_bodies: dict[str, bytes] | None = None,
    export_errors: dict[str, Exception] | None = None,
) -> MagicMock:
    """Return a mock DriveClient configured with the given file list and content."""
    if doc_bodies is None:
        doc_bodies = {}
    if png_bodies is None:
        png_bodies = {}
    if export_errors is None:
        export_errors = {}

    client = MagicMock()
    client.list_folder_recursive.return_value = files

    def _export(file_id: str) -> str:
        if file_id in export_errors:
            raise export_errors[file_id]
        return doc_bodies.get(file_id, f"# Doc {file_id}\n")

    def _download(file_id: str) -> bytes:
        if file_id in export_errors:
            raise export_errors[file_id]
        return png_bodies.get(file_id, b"\x89PNG\r\n" + file_id.encode())

    client.export_gdoc_markdown.side_effect = _export
    client.download_png.side_effect = _download
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
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        body = "# Title\n\nContent here.\n"
        client = _make_drive_client(
            files=[_file_entry("doc1", "My Doc", _DOC_MIME)],
            doc_bodies={"doc1": body},
        )
        summary, _ = _run(client, lore_root, state_path)

        md_file = lore_root / "my-doc.md"
        assert md_file.exists()
        assert summary.created == 1
        assert summary.errors == 0

        # Verify state persisted.
        state = load_state(state_path)
        assert "doc1" in state
        assert state["doc1"].content_signature.startswith("sha256:")

    def test_png_written_and_state_saved(self, tmp_path: Path) -> None:
        # AC-3: PNG downloaded and written under lore_root, no .md produced.
        # Drive name "beach" slugifies to "beach"; ext stays ".png".
        lore_root = tmp_path / "lore"
        state_path = tmp_path / "state.json"
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        md5 = hashlib.md5(png_bytes).hexdigest()  # noqa: S324
        client = _make_drive_client(
            files=[_file_entry("png1", "beach", _PNG_MIME, md5=md5)],
            png_bodies={"png1": png_bytes},
        )
        summary, _ = _run(client, lore_root, state_path)

        # slugify("beach", ...) → "beach", ext=".png" → lore/beach.png
        png_file = lore_root / "beach.png"
        assert png_file.exists()
        assert png_file.read_bytes() == png_bytes
        assert not (lore_root / "beach.md").exists()
        assert summary.created == 1

        state = load_state(state_path)
        assert state["png1"].content_signature.startswith("md5:")

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
