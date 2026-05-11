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
from gdrive_export.gsheet_renderer import (
    build_tab_source_id,
    build_tab_title,
    render_tab_markdown,
    resolve_tab_collisions,
)
from gdrive_export.gslide_renderer import render_presentation_markdown
from gdrive_export.normalize import normalize_body
from gdrive_export.paths import resolve_local_path
from gdrive_export.rename import rename_local_file
from gdrive_export.slugify import slugify
from gdrive_export.state import StateEntry, compute_body_hash, load_state, save_state

_DOC_MIME = "application/vnd.google-apps.document"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_SLIDES_MIME = "application/vnd.google-apps.presentation"
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


@dataclass
class SyncContext:
    """Shared state threaded through file-processing helpers.

    Groups the parameters that are constant for an entire sync run and
    needed by multiple dispatch functions (MED-3 clean-code.md ≤4 params).
    """

    drive_client: DriveClient
    new_state: dict[str, StateEntry]
    counts: SummaryCounts
    dry_run: bool
    state: dict[str, StateEntry]
    taken_paths: set[Path]
    lore_root: Path


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
    sheet_ids = {f["id"] for f in files if f.get("mimeType") == _SHEET_MIME}

    ctx = SyncContext(
        drive_client=drive_client,
        new_state=new_state,
        counts=counts,
        dry_run=dry_run,
        state=state,
        taken_paths=taken_paths,
        lore_root=lore_root,
    )

    # AC-7/AC-8: handle archived (absent from Drive) before processing new/existing.
    _handle_archived(state, drive_ids, sheet_ids, ctx)

    for file in files:
        file_id: str = file["id"]
        mime: str = file["mimeType"]
        name: str = file["name"]
        components: list[str] = file.get("drive_path_components", [])
        drive_md5: str | None = file.get("md5Checksum")

        # MED-1: gsheet local_path is NOT allocated here; the phantom reservation
        # was causing spurious -<fileid> suffixes on colliding gdoc/png in the same run.
        # Tab paths are allocated inside _process_gsheet per-tab.
        if mime == _SHEET_MIME:
            _process_gsheet(file_id, name, components, state.get(file_id), ctx)
            continue

        ext = ".md" if mime in (_SLIDES_MIME, _DOC_MIME) else ".png"
        local_path = resolve_local_path(
            components, slugify(name, file_id), ext, taken_paths, file_id, lore_root
        )
        taken_paths.add(local_path)
        _process_file(
            file_id=file_id, mime=mime, name=name, components=components,
            local_path=local_path, existing=state.get(file_id),
            drive_md5=drive_md5, ctx=ctx,
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
    sheet_ids: set[str],
    ctx: SyncContext,
) -> None:
    """AC-7/AC-8: for each state entry absent from Drive, archive or mark unchanged.

    Composite keys '<file_id>#<gid>' (gsheet tabs) are handled separately:
    archived if (a) parent file_id absent from Drive, or (b) parent present but
    tab gid absent from current spreadsheet tab list.
    """
    # Collect current gid sets per sheet file (fetched lazily).
    current_tab_gids: dict[str, set[int]] = {}

    for state_key, entry in state.items():
        if "#" in state_key:
            file_id, gid_str = state_key.split("#", 1)
            _handle_archived_tab(
                state_key=state_key, file_id=file_id, gid=int(gid_str),
                entry=entry, drive_ids=drive_ids, sheet_ids=sheet_ids,
                current_tab_gids=current_tab_gids,
                ctx=ctx,
            )
        else:
            if state_key in drive_ids:
                continue
            _archive_entry(state_key, entry, ctx.new_state, ctx.counts, ctx.dry_run)


def _handle_archived_tab(
    state_key: str,
    file_id: str,
    gid: int,
    entry: StateEntry,
    drive_ids: set[str],
    sheet_ids: set[str],
    current_tab_gids: dict[str, set[int]],
    ctx: SyncContext,
) -> None:
    """Archive a gsheet tab state entry if its parent or gid is gone."""
    if file_id not in drive_ids:
        # Parent spreadsheet deleted → archive tab (AC-7 whole-workbook deletion)
        _archive_entry(state_key, entry, ctx.new_state, ctx.counts, ctx.dry_run)
        return

    if file_id not in sheet_ids:
        # Parent exists but is no longer a sheet (edge case) → keep
        ctx.new_state[state_key] = entry
        return

    if file_id not in current_tab_gids:
        # Fetch current tabs for this file (lazy, once per file)
        try:
            tabs = ctx.drive_client.get_spreadsheet_tabs(file_id)
            current_tab_gids[file_id] = {int(t["sheetId"]) for t in tabs}
        except DriveApiError:
            # Cannot fetch → keep existing state (conservative)
            ctx.new_state[state_key] = entry
            return

    if gid not in current_tab_gids[file_id]:
        # Tab gid no longer in spreadsheet → archive per AC-7
        _archive_entry(state_key, entry, ctx.new_state, ctx.counts, ctx.dry_run)
    else:
        # Tab still present; will be re-processed in main loop
        ctx.new_state[state_key] = entry


def _archive_entry(
    state_key: str,
    entry: StateEntry,
    new_state: dict[str, StateEntry],
    counts: SummaryCounts,
    dry_run: bool,  # noqa: FBT001
) -> None:
    """Mark a state entry as archived (idempotent)."""
    counts.total += 1
    local_path = Path(entry.local_path)

    if entry.archived_at is not None:
        new_state[state_key] = entry
        counts.unchanged += 1
        _log_file(state_key, str(local_path), "would_unchanged" if dry_run else "unchanged")
        return

    archived_at = _now_iso()
    if dry_run:
        new_state[state_key] = entry
        counts.archived += 1
        _log_file(state_key, str(local_path), "would_archive")
        return

    if local_path.suffix == ".md" and local_path.exists():
        _set_archived_frontmatter(local_path, archived_at)

    new_state[state_key] = StateEntry(
        local_path=entry.local_path,
        content_signature=entry.content_signature,
        last_exported_at=entry.last_exported_at,
        body_hash=entry.body_hash,
        archived_at=archived_at,
    )
    counts.archived += 1
    _log_file(state_key, str(local_path), "archived")


def _process_file(
    file_id: str,
    mime: str,
    name: str,
    components: list[str],
    local_path: Path,
    existing: StateEntry | None,
    drive_md5: str | None,
    ctx: SyncContext,
) -> None:
    """Process a single non-gsheet Drive file: fetch, diff, dispatch.

    gsheet dispatch is handled separately in run_sync (before this function).
    All MIMEs here increment counts.total once.
    """
    try:
        if mime == _DOC_MIME:
            ctx.counts.total += 1
            _process_gdoc(file_id, name, components, local_path, existing, ctx)
        elif mime == _SLIDES_MIME:
            ctx.counts.total += 1
            _process_gslide(file_id, name, components, local_path, existing, ctx)
        else:
            ctx.counts.total += 1
            _process_png(file_id, local_path, existing, ctx, drive_md5=drive_md5)
    except (DriveApiError, FrontmatterMergeError, OSError, ValueError) as exc:
        ctx.counts.errors += 1
        _log_file(file_id, str(local_path),
                  "would_error" if ctx.dry_run else "error", reason=str(exc))


def _process_gdoc(
    file_id: str,
    name: str,
    components: list[str],
    local_path: Path,
    existing: StateEntry | None,
    ctx: SyncContext,
) -> None:
    """Export a Google Doc and write/update local .md."""
    body = normalize_body(ctx.drive_client.export_gdoc_markdown(file_id))

    if len(body.encode("utf-8")) > _ONE_MIB:
        ctx.counts.errors += 1
        _log_file(file_id, str(local_path), "would_error" if ctx.dry_run else "error",
                  reason="exported size exceeds 1 MiB cap")
        return

    new_sig = f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}"
    new_body_hash = compute_body_hash(body)
    now = _now_iso()
    drive_path = "/".join([*components, name])

    is_rename = existing is not None and Path(existing.local_path) != local_path
    if existing is not None and existing.content_signature == new_sig and not is_rename:
        # AC-11: unchanged content and path — no rewrite.
        ctx.new_state[file_id] = existing
        ctx.counts.unchanged += 1
        _log_file(file_id, str(local_path), "would_unchanged" if ctx.dry_run else "unchanged")
        return

    status = _gdoc_status_and_rename(file_id, local_path, existing, ctx.counts, ctx.dry_run)

    if not ctx.dry_run:
        existing_fm = _read_existing_frontmatter(local_path)
        _check_local_drift_before_write(file_id, local_path, existing, new_body_hash)
        _write_gdoc(local_path, name, file_id, drive_path, now, body, existing_fm)
        ctx.new_state[file_id] = StateEntry(
            local_path=str(local_path), content_signature=new_sig,
            last_exported_at=now, body_hash=new_body_hash, archived_at=None,
        )
    else:
        ctx.new_state[file_id] = existing or StateEntry(
            local_path=str(local_path), content_signature=new_sig,
            last_exported_at=now, body_hash=new_body_hash, archived_at=None,
        )

    _log_file(file_id, str(local_path), status)


def _fetch_tab_content(
    file_id: str,
    tab_title: str,
    name: str,
    gid: int,
    local_path: Path,
    ctx: SyncContext,
) -> str | None:
    """Fetch and render tab content; return None and log error on failure."""
    source_id = build_tab_source_id(file_id, gid)
    try:
        values = ctx.drive_client.get_sheet_values(file_id, tab_title)
        body = render_tab_markdown(name, tab_title, values, file_id=file_id, gid=gid)
        return normalize_body(body)
    except (DriveApiError, OSError) as exc:
        ctx.counts.total += 1
        ctx.counts.errors += 1
        _log_file(source_id, str(local_path), "would_error" if ctx.dry_run else "error",
                  reason=str(exc))
        return None


def _apply_tab_state(
    source_id: str,
    local_path: Path,
    body_norm: str,
    tab_title: str,
    name: str,
    drive_path_prefix: str,
    gid: int,
    existing: StateEntry | None,
    ctx: SyncContext,
) -> None:
    """Write tab .md and update state; called after size and drift checks pass."""
    new_sig = f"sha256:{hashlib.sha256(body_norm.encode('utf-8')).hexdigest()}"
    new_body_hash = compute_body_hash(body_norm)
    now = _now_iso()
    title = build_tab_title(name, tab_title)
    # LOW-6: escape '/' in tab_title so drive_path remains unambiguous
    safe_tab_title = tab_title.replace("/", "\\/")
    drive_path = f"{drive_path_prefix}/{safe_tab_title}"

    ctx.counts.total += 1
    is_rename = existing is not None and Path(existing.local_path) != local_path
    if existing is not None and existing.content_signature == new_sig and not is_rename:
        ctx.new_state[source_id] = existing
        ctx.counts.unchanged += 1
        _log_file(source_id, str(local_path), "would_unchanged" if ctx.dry_run else "unchanged",
                  tab_gid=gid)
        return

    status = _tab_status_and_rename(source_id, local_path, existing, ctx.counts, ctx.dry_run)

    if not ctx.dry_run:
        existing_fm = _read_existing_frontmatter(local_path)
        _write_gdoc(local_path, title, source_id, drive_path, now, body_norm, existing_fm)
        ctx.new_state[source_id] = StateEntry(
            local_path=str(local_path), content_signature=new_sig,
            last_exported_at=now, body_hash=new_body_hash, archived_at=None,
        )
    else:
        ctx.new_state[source_id] = existing or StateEntry(
            local_path=str(local_path), content_signature=new_sig,
            last_exported_at=now, body_hash=new_body_hash, archived_at=None,
        )
    _log_file(source_id, str(local_path), status, tab_gid=gid)


def _archive_removed_tabs(
    file_id: str,
    current_gids: set[int],
    ctx: SyncContext,
) -> None:
    """Archive state entries for tabs whose gid is no longer in current_gids."""
    for state_key in list(ctx.state.keys()):
        if not state_key.startswith(f"{file_id}#"):
            continue
        gid_str = state_key.split("#", 1)[1]
        if int(gid_str) not in current_gids:
            entry = ctx.state[state_key]
            _archive_entry(state_key, entry, ctx.new_state, ctx.counts, ctx.dry_run)


def _process_gsheet(
    file_id: str,
    name: str,
    components: list[str],
    existing_file_entry: StateEntry | None,
    ctx: SyncContext,
) -> None:
    """Export all tabs of a gsheet, one .md per tab (AC-2/3/4/5/6/7/8/13/17).

    HIGH-1: DriveApiError on get_spreadsheet_tabs increments counts.errors and
    exits the function so __main__.py exits 1 per failure-modes spec L52.
    """
    try:
        tabs = ctx.drive_client.get_spreadsheet_tabs(file_id)
    except (DriveApiError, OSError) as exc:
        # HIGH-1: workbook-level failure must count as an error (spec L52 quota rule).
        ctx.counts.total += 1
        ctx.counts.errors += 1
        _log_file(file_id, str(ctx.lore_root), "would_error" if ctx.dry_run else "error",
                  reason=str(exc))
        return

    slug_pairs = resolve_tab_collisions(tabs)
    drive_path_prefix = "/".join([*components, name])
    current_gids: set[int] = set()

    for tab, tab_slug in slug_pairs:
        gid = int(tab["sheetId"])
        tab_title = str(tab["title"])
        source_id = build_tab_source_id(file_id, gid)
        sheet_slug = slugify(name, file_id)
        tab_filename = f"{sheet_slug}--{tab_slug}.md"
        local_path = (
            ctx.lore_root.joinpath(*[slugify(c, file_id) for c in components]) / tab_filename
        )
        ctx.taken_paths.add(local_path)
        current_gids.add(gid)
        existing = ctx.state.get(source_id)

        body_norm = _fetch_tab_content(file_id, tab_title, name, gid, local_path, ctx)
        if body_norm is None:
            continue

        if len(body_norm.encode("utf-8")) > _ONE_MIB:
            ctx.counts.total += 1
            ctx.counts.errors += 1
            _log_file(source_id, str(local_path), "would_error" if ctx.dry_run else "error",
                      reason="exported size exceeds 1 MiB cap")
            continue

        _apply_tab_state(
            source_id=source_id,
            local_path=local_path,
            body_norm=body_norm,
            tab_title=tab_title,
            name=name,
            drive_path_prefix=drive_path_prefix,
            gid=gid,
            existing=existing,
            ctx=ctx,
        )


def _tab_status_and_rename(
    source_id: str,
    local_path: Path,
    existing: StateEntry | None,
    counts: SummaryCounts,
    dry_run: bool,  # noqa: FBT001
) -> str:
    """Return status string for a gsheet tab write; rename local file if needed."""
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


def _process_gslide(
    file_id: str,
    name: str,
    components: list[str],
    local_path: Path,
    existing: StateEntry | None,
    ctx: SyncContext,
) -> None:
    """Export a Google Slides presentation as a single .md file (AC-9/10/11/16/17)."""
    presentation = ctx.drive_client.get_presentation(file_id)
    body, hidden_indices = render_presentation_markdown(name, presentation, file_id=file_id)
    body_norm = normalize_body(body)

    for original_idx in hidden_indices:
        _log({"event": "gdrive_sync.slide_hidden_skipped",
              "source_id": file_id, "slide_index_original": original_idx})

    if len(body_norm.encode("utf-8")) > _ONE_MIB:
        ctx.counts.errors += 1
        _log_file(file_id, str(local_path), "would_error" if ctx.dry_run else "error",
                  reason="exported size exceeds 1 MiB cap")
        return

    _apply_gslide_state(file_id, name, components, local_path, existing, body_norm, ctx)


def _apply_gslide_state(
    file_id: str,
    name: str,
    components: list[str],
    local_path: Path,
    existing: StateEntry | None,
    body_norm: str,
    ctx: SyncContext,
) -> None:
    """Diff, write, and update state for a gslide file."""
    new_sig = f"sha256:{hashlib.sha256(body_norm.encode('utf-8')).hexdigest()}"
    new_body_hash = compute_body_hash(body_norm)
    now = _now_iso()
    drive_path = "/".join([*components, name])

    is_rename = existing is not None and Path(existing.local_path) != local_path
    if existing is not None and existing.content_signature == new_sig and not is_rename:
        ctx.new_state[file_id] = existing
        ctx.counts.unchanged += 1
        _log_file(file_id, str(local_path), "would_unchanged" if ctx.dry_run else "unchanged")
        return

    status = _gdoc_status_and_rename(file_id, local_path, existing, ctx.counts, ctx.dry_run)

    if not ctx.dry_run:
        existing_fm = _read_existing_frontmatter(local_path)
        _write_gdoc(local_path, name, file_id, drive_path, now, body_norm, existing_fm)
        ctx.new_state[file_id] = StateEntry(
            local_path=str(local_path), content_signature=new_sig,
            last_exported_at=now, body_hash=new_body_hash, archived_at=None,
        )
    else:
        ctx.new_state[file_id] = existing or StateEntry(
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
    ctx: SyncContext,
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
            ctx.new_state[file_id] = existing
            ctx.counts.unchanged += 1
            _log_file(file_id, str(local_path), "would_unchanged" if ctx.dry_run else "unchanged")
            return

    png_bytes = ctx.drive_client.download_png(file_id)

    if len(png_bytes) > _ONE_MIB:
        ctx.counts.errors += 1
        _log_file(file_id, str(local_path), "would_error" if ctx.dry_run else "error",
                  reason="exported size exceeds 1 MiB cap")
        return

    # Fallback to client-side md5 when Drive field is absent (defense-in-depth).
    if drive_md5 is None:
        new_sig = f"md5:{hashlib.md5(png_bytes).hexdigest()}"  # noqa: S324
        if existing is not None and existing.content_signature == new_sig:
            ctx.new_state[file_id] = existing
            ctx.counts.unchanged += 1
            _log_file(file_id, str(local_path), "would_unchanged" if ctx.dry_run else "unchanged")
            return

    now = _now_iso()
    prefix = "would_" if ctx.dry_run else ""
    if existing is None:
        status = f"{prefix}create" if ctx.dry_run else "created"
        ctx.counts.created += 1
    else:
        old_path = Path(existing.local_path)
        if old_path != local_path:
            status = f"{prefix}renamed" if not ctx.dry_run else f"{prefix}rename"
            ctx.counts.renamed += 1
            if not ctx.dry_run:
                rename_local_file(old_path, local_path)
                _log({"event": "gdrive_sync.image_renamed", "source_id": file_id,
                      "old_path": str(old_path), "new_path": str(local_path)})
        else:
            status = f"{prefix}updated" if not ctx.dry_run else f"{prefix}update"
            ctx.counts.updated += 1

    if not ctx.dry_run:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(png_bytes)
        ctx.new_state[file_id] = StateEntry(
            local_path=str(local_path), content_signature=new_sig,
            last_exported_at=now, body_hash="", archived_at=None,
        )
    else:
        ctx.new_state[file_id] = existing or StateEntry(
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
    tab_gid: int | None = None,
) -> None:
    entry: dict[str, Any] = {"event": "gdrive_sync.file", "source_id": source_id,
                              "local_path": local_path, "status": status}
    if reason is not None:
        entry["reason"] = reason
    if tab_gid is not None:
        entry["tab_gid"] = tab_gid
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
