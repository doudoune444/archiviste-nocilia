"""Local filesystem path resolution for Drive-synced files."""

from pathlib import Path

from gdrive_export.slugify import slugify


def resolve_local_path(
    drive_path_components: list[str],
    slug: str,
    ext: str,
    taken_paths: set[Path],
    drive_file_id: str,
    root: Path,
) -> Path:
    """Resolve a local filesystem path for a Drive file, avoiding collisions.

    Each path component and the slug are individually slugified (AC-2).
    If the resulting path is already taken, the stem is suffixed with ``-<drive_file_id[:8]>``.
    The returned path is always under *root* (assert ``is_relative_to(root)``).

    Invariant: collision resolution is deterministic given the same inputs.
    """
    safe_components = [slugify(part, drive_file_id) for part in drive_path_components]
    safe_slug = slugify(slug, drive_file_id)
    candidate = root.joinpath(*safe_components) / f"{safe_slug}{ext}"
    _assert_under_root(candidate, root)
    if candidate not in taken_paths:
        return candidate
    suffixed_slug = f"{safe_slug}-{drive_file_id[:8]}"
    candidate = root.joinpath(*safe_components) / f"{suffixed_slug}{ext}"
    _assert_under_root(candidate, root)
    return candidate


def _assert_under_root(path: Path, root: Path) -> None:
    """Assert that *path* stays under *root* to prevent path traversal."""
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        msg = f"Path traversal detected: {path!r} escapes root {root!r}"
        raise ValueError(msg)
