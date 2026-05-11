"""Sync orchestrator: diff Drive state vs local state, dispatch file operations.

AC-4/5/6/8/9/10/11/12/13/14/15/16/17/20.
No Drive API imports here (only drive_client.DriveClient stub is injected).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from gdrive_export.drive_client import DriveApiError, DriveClient
from gdrive_export.frontmatter_merge import FrontmatterMergeError
from gdrive_export.normalize import normalize_body
from gdrive_export.paths import resolve_local_path
from gdrive_export.rename import rename_local_file
from gdrive_export.slugify import slugify
from gdrive_export.state import StateEntry, compute_body_hash, load_state, save_state

_DOC_MIME = "application/vnd.google-apps.document"
_ONE_MIB = 1024 * 1024

# Strict frontmatter key order per AC-4.
_FM_KEY_ORDER = [
    "title", "source", "source_id", "drive_path",
    "exported_at", "archived", "archived_at", "tags", "access_tier",
]


@dataclass
class SummaryCounts:
    """Counts of files by status for the summary log."""

    total: int = 0
    created: int = 0
    updated: int = 0
    renamed: int = 0
    archived: int = 0
    unchanged: int = 0
    errors: int = 0


def run_sync(
    drive_client: DriveClient,
    root_folder_id: str,
    lore_root: Path,
    state_path: Path,
    *,
    dry_run: bool = False,
) -> SummaryCounts:
    """Run one sync cycle: list Drive → diff → dispatch per file → save state."""
    start_ms = time.monotonic()
    state = _load_state_or_first_run(state_path, root_folder_id, dry_run=dry_run)

    files = sorted(drive_client.list_folder_recursive(root_folder_id), key=lambda f: f["id"])

    # taken_paths: allocated in this run only (not pre-seeded from state,
    # to avoid a file's own path colliding with itself on rename-free runs).
    taken_paths: set[Path] = set()
    counts = SummaryCounts()
    new_state: dict[str, StateEntry] = {}
    drive_ids = {f["id"] for f in files}

    # AC-8: handle archived (absent from Drive) before processing new/existing.
    _handle_archived(state, drive_ids, new_state, counts, dry_run)

    for file in files:
        file_id: str = file["id"]
        mime: str = file["mimeType"]
        name: str = file["name"]
        components: list[str] = file.get("drive_path_components", [])
        ext = ".md" if mime == _DOC_MIME else ".png"
        local_path = resolve_local_path(
            components, slugify(name, file_id), ext, taken_paths, file_id, lore_root
        )
        taken_paths.add(local_path)
        _process_file(
            file_id=file_id, mime=mime, name=name, components=components,
            local_path=local_path, existing=state.get(file_id),
            drive_client=drive_client, new_state=new_state, counts=counts,
            dry_run=dry_run,
            drive_md5=file.get("md5Checksum"),
        )

    if not dry_run:
        save_state(state_path, new_state)

    _log_summary(counts, int((time.monotonic() - start_ms) * 1000), dry_run)
    return counts


def _load_state_or_first_run(
    state_path: Path,
    root_folder_id: str,
    *,
    dry_run: bool,
) -> dict[str, StateEntry]:
    """Load state; emit start + optional first_run logs per AC-13."""
    is_first_run = not state_path.exists()
    state = load_state(state_path)
    _log({"event": "gdrive_sync.start", "root_folder_id": root_folder_id,
          "dry_run": dry_run, "state_size": len(state)})
    if is_first_run:
        _log({"event": "gdrive_sync.first_run"})
    return state


def _handle_archived(
    state: dict[str, StateEntry],
    drive_ids: set[str],
    new_state: dict[str, StateEntry],
    counts: SummaryCounts,
    dry_run: bool,  # noqa: FBT001
) -> None:
    """AC-8: for each state entry absent from Drive, archive or mark unchanged."""
    for file_id, entry in state.items():
        if file_id in drive_ids:
            continue
        counts.total += 1
        local_path = Path(entry.local_path)

        if entry.archived_at is not None:
            # AC-8 idempotence: already archived, do not touch mtime.
            new_state[file_id] = entry
            counts.unchanged += 1
            _log_file(file_id, str(local_path), "would_unchanged" if dry_run else "unchanged")
            continue

        archived_at = _now_iso()
        if dry_run:
            new_state[file_id] = entry
            counts.archived += 1
            _log_file(file_id, str(local_path), "would_archive")
            continue

        if local_path.suffix == ".md" and local_path.exists():
            _set_archived_frontmatter(local_path, archived_at)

        new_state[file_id] = StateEntry(
            local_path=entry.local_path,
            content_signature=entry.content_signature,
            last_exported_at=entry.last_exported_at,
            body_hash=entry.body_hash,
            archived_at=archived_at,
        )
        counts.archived += 1
        _log_file(file_id, str(local_path), "archived")


def _process_file(
    file_id: str,
    mime: str,
    name: str,
    components: list[str],
    local_path: Path,
    existing: StateEntry | None,
    drive_client: DriveClient,
    new_state: dict[str, StateEntry],
    counts: SummaryCounts,
    dry_run: bool,  # noqa: FBT001
    drive_md5: str | None = None,
) -> None:
    """Process a single Drive file: fetch, diff, dispatch."""
    counts.total += 1
    try:
        if mime == _DOC_MIME:
            _process_gdoc(
                file_id, name, components, local_path, existing,
                drive_client, new_state, counts, dry_run,
            )
        else:
            _process_png(
                file_id, local_path, existing, drive_client, new_state, counts, dry_run,
                drive_md5=drive_md5,
            )
    except (DriveApiError, FrontmatterMergeError, OSError, ValueError) as exc:
        counts.errors += 1
        _log_file(file_id, str(local_path), "would_error" if dry_run else "error", reason=str(exc))


def _process_gdoc(
    file_id: str,
    name: str,
    components: list[str],
    local_path: Path,
    existing: StateEntry | None,
    drive_client: DriveClient,
    new_state: dict[str, StateEntry],
    counts: SummaryCounts,
    dry_run: bool,  # noqa: FBT001
) -> None:
    """Export a Google Doc and write/update local .md."""
    body = normalize_body(drive_client.export_gdoc_markdown(file_id))

    if len(body.encode("utf-8")) > _ONE_MIB:
        counts.errors += 1
        _log_file(file_id, str(local_path), "would_error" if dry_run else "error",
                  reason="exported size exceeds 1 MiB cap")
        return

    new_sig = f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}"
    new_body_hash = compute_body_hash(body)
    now = _now_iso()
    drive_path = "/".join([*components, name])

    is_rename = existing is not None and Path(existing.local_path) != local_path
    if existing is not None and existing.content_signature == new_sig and not is_rename:
        # AC-11: unchanged content and path — no rewrite.
        new_state[file_id] = existing
        counts.unchanged += 1
        _log_file(file_id, str(local_path), "would_unchanged" if dry_run else "unchanged")
        return

    status = _gdoc_status_and_rename(file_id, local_path, existing, counts, dry_run)

    if not dry_run:
        existing_fm = _read_existing_frontmatter(local_path)
        _check_local_drift_before_write(file_id, local_path, existing, new_body_hash)
        _write_gdoc(local_path, name, file_id, drive_path, now, body, existing_fm)
        new_state[file_id] = StateEntry(
            local_path=str(local_path), content_signature=new_sig,
            last_exported_at=now, body_hash=new_body_hash, archived_at=None,
        )
    else:
        new_state[file_id] = existing or StateEntry(
            local_path=str(local_path), content_signature=new_sig,
            last_exported_at=now, body_hash=new_body_hash, archived_at=None,
        )

    _log_file(file_id, str(local_path), status)


def _gdoc_status_and_rename(
    file_id: str,
    local_path: Path,
    existing: StateEntry | None,
    counts: SummaryCounts,
    dry_run: bool,  # noqa: FBT001
) -> str:
    """Return status string and perform rename if needed; update counts."""
    prefix = "would_" if dry_run else ""
    if existing is None:
        counts.created += 1
        return f"{prefix}create" if dry_run else "created"
    old_path = Path(existing.local_path)
    if old_path != local_path:
        counts.renamed += 1
        if not dry_run:
            rename_local_file(old_path, local_path)
        return f"{prefix}renamed" if not dry_run else f"{prefix}rename"
    counts.updated += 1
    return f"{prefix}updated" if not dry_run else f"{prefix}update"


def _check_local_drift_before_write(
    file_id: str,
    local_path: Path,
    existing: StateEntry | None,
    incoming_body_hash: str,
) -> None:
    """AC-20: detect human edit of local file; log warning before overwriting."""
    if existing is None or not local_path.exists() or not existing.body_hash:
        return
    content = local_path.read_text(encoding="utf-8")
    body_part = content
    if content.startswith("---\n"):
        try:
            fm_end = content.index("\n---\n", 4)
            body_part = content[fm_end + 5:]
        except ValueError:
            pass
    current_hash = compute_body_hash(body_part)
    if current_hash != existing.body_hash and incoming_body_hash != existing.body_hash:  # noqa: PLR1714
        _log({"event": "gdrive_sync.local_drift_overwritten",
              "source_id": file_id, "local_path": str(local_path)})


def _process_png(
    file_id: str,
    local_path: Path,
    existing: StateEntry | None,
    drive_client: DriveClient,
    new_state: dict[str, StateEntry],
    counts: SummaryCounts,
    dry_run: bool,  # noqa: FBT001
    drive_md5: str | None = None,
) -> None:
    """Download a PNG and write it locally.

    AC-11: content_signature = md5:<md5Checksum> from Drive API field (canonical).
    Short-circuits download when signature matches existing state.
    """
    # AC-11: use Drive-native md5Checksum as canonical signature.
    # This avoids a download when the file is unchanged.
    if drive_md5 is not None:
        new_sig = f"md5:{drive_md5}"
        if existing is not None and existing.content_signature == new_sig:
            new_state[file_id] = existing
            counts.unchanged += 1
            _log_file(file_id, str(local_path), "would_unchanged" if dry_run else "unchanged")
            return

    png_bytes = drive_client.download_png(file_id)

    if len(png_bytes) > _ONE_MIB:
        counts.errors += 1
        _log_file(file_id, str(local_path), "would_error" if dry_run else "error",
                  reason="exported size exceeds 1 MiB cap")
        return

    # Fallback to client-side md5 when Drive field is absent (defense-in-depth).
    if drive_md5 is None:
        new_sig = f"md5:{hashlib.md5(png_bytes).hexdigest()}"  # noqa: S324
        if existing is not None and existing.content_signature == new_sig:
            new_state[file_id] = existing
            counts.unchanged += 1
            _log_file(file_id, str(local_path), "would_unchanged" if dry_run else "unchanged")
            return

    now = _now_iso()
    prefix = "would_" if dry_run else ""
    if existing is None:
        status = f"{prefix}create" if dry_run else "created"
        counts.created += 1
    else:
        old_path = Path(existing.local_path)
        if old_path != local_path:
            status = f"{prefix}renamed" if not dry_run else f"{prefix}rename"
            counts.renamed += 1
            if not dry_run:
                rename_local_file(old_path, local_path)
                _log({"event": "gdrive_sync.image_renamed", "source_id": file_id,
                      "old_path": str(old_path), "new_path": str(local_path)})
        else:
            status = f"{prefix}updated" if not dry_run else f"{prefix}update"
            counts.updated += 1

    if not dry_run:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(png_bytes)
        new_state[file_id] = StateEntry(
            local_path=str(local_path), content_signature=new_sig,
            last_exported_at=now, body_hash="", archived_at=None,
        )
    else:
        new_state[file_id] = existing or StateEntry(
            local_path=str(local_path), content_signature=new_sig,
            last_exported_at=now, body_hash="", archived_at=None,
        )
    _log_file(file_id, str(local_path), status)


def _write_gdoc(
    local_path: Path,
    title: str,
    source_id: str,
    drive_path: str,
    exported_at: str,
    body: str,
    existing_fm_yaml: str | None,
) -> None:
    """Write frontmatter + body to *local_path*."""
    script_managed: dict[str, Any] = {
        "title": title, "source": "gdrive", "source_id": source_id,
        "drive_path": drive_path, "exported_at": exported_at,
        "archived": False, "archived_at": None,
    }
    merged = _build_ordered_frontmatter(existing_fm_yaml, script_managed)
    fm_str = yaml.safe_dump(merged, default_flow_style=False, allow_unicode=True, sort_keys=False)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(f"---\n{fm_str}---\n{body}", encoding="utf-8")


def _build_ordered_frontmatter(
    existing_yaml: str | None,
    script_managed: dict[str, Any],
) -> dict[str, Any]:
    """Build frontmatter dict with strict key order per AC-4."""
    existing: dict[str, Any] = {}
    if existing_yaml:
        parsed = yaml.safe_load(existing_yaml)
        if isinstance(parsed, dict):
            existing = parsed

    ordered: dict[str, Any] = {}
    for key in _FM_KEY_ORDER:
        if key in script_managed:
            ordered[key] = script_managed[key]
        elif key in existing:
            ordered[key] = existing[key]
        elif key == "tags":
            ordered[key] = []
        elif key == "access_tier":
            ordered[key] = "public"
        elif key == "archived":
            ordered[key] = False
        elif key == "archived_at":
            ordered[key] = None

    for key, val in existing.items():
        if key not in ordered and key not in script_managed:
            ordered[key] = val

    if ordered.get("archived_at") is None:
        del ordered["archived_at"]

    return ordered


def _set_archived_frontmatter(local_path: Path, archived_at: str) -> None:
    """Set archived: true and archived_at in the existing .md frontmatter."""
    content = local_path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        return
    fm_end = content.index("\n---\n", 4)
    fm_yaml = content[4:fm_end]
    body_part = content[fm_end + 5:]
    fm: dict[str, Any] = yaml.safe_load(fm_yaml) or {}
    fm["archived"] = True
    fm["archived_at"] = archived_at
    new_fm = yaml.safe_dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    local_path.write_text(f"---\n{new_fm}---\n{body_part}", encoding="utf-8")


def _read_existing_frontmatter(local_path: Path) -> str | None:
    """Return the YAML frontmatter string from *local_path*, or None if absent."""
    if not local_path.exists():
        return None
    content = local_path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        return None
    try:
        fm_end = content.index("\n---\n", 4)
        return content[4:fm_end]
    except ValueError:
        return None


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(data: dict[str, Any]) -> None:
    print(json.dumps(data))  # noqa: T201


def _log_file(
    source_id: str,
    local_path: str,
    status: str,
    reason: str | None = None,
) -> None:
    entry: dict[str, Any] = {"event": "gdrive_sync.file", "source_id": source_id,
                              "local_path": local_path, "status": status}
    if reason is not None:
        entry["reason"] = reason
    _log(entry)


def _log_summary(counts: SummaryCounts, duration_ms: int, dry_run: bool) -> None:  # noqa: FBT001
    if dry_run:
        _log({"event": "gdrive_sync.summary", "total": counts.total,
              "would_created": counts.created, "would_updated": counts.updated,
              "would_renamed": counts.renamed, "would_archived": counts.archived,
              "would_unchanged": counts.unchanged, "would_errors": counts.errors,
              "duration_ms": duration_ms})
    else:
        _log({"event": "gdrive_sync.summary", "total": counts.total,
              "created": counts.created, "updated": counts.updated,
              "renamed": counts.renamed, "archived": counts.archived,
              "unchanged": counts.unchanged, "errors": counts.errors,
              "duration_ms": duration_ms})
