"""Local file rename using pure pathlib — no subprocess, no git mv."""

from pathlib import Path


def rename_local_file(old_path: Path, new_path: Path) -> None:
    """Rename *old_path* to *new_path* using ``Path.rename()``.

    - Creates missing parent directories of *new_path*.
    - Raises ``FileNotFoundError`` if *old_path* does not exist.
    - Raises ``FileExistsError`` if *new_path* already exists.
    - No subprocess, no git commands; git detects the rename via similarity ≥ 50%.
    """
    if not old_path.exists():
        raise FileNotFoundError(f"Source file not found: {old_path}")
    if new_path.exists():
        raise FileExistsError(f"Destination already exists: {new_path}")
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)
