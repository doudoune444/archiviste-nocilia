"""State file I/O for tracking exported Drive files."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


class StateCorruptedError(Exception):
    """Raised when the state JSON file cannot be parsed or has an invalid shape."""


@dataclass
class StateEntry:
    """Represents a single tracked Drive file in the sync state."""

    local_path: str
    content_signature: str
    last_exported_at: str
    body_hash: str
    archived_at: str | None
    images: dict[str, str] = field(default_factory=dict)


def compute_body_hash(body: str) -> str:
    """Return the SHA-256 hex digest of *body* encoded as UTF-8.

    Invariant: deterministic — same body always yields same hash.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def load_state(path: Path) -> dict[str, StateEntry]:
    """Load state from *path*; return ``{}`` if absent; raise ``StateCorruptedError`` if invalid.

    Invariant: absent file → first-run empty state, never raises.
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateCorruptedError(f"State file is not valid JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise StateCorruptedError(f"State file root must be a JSON object: {path}")
    result: dict[str, StateEntry] = {}
    for file_id, entry in raw.items():
        if not isinstance(entry, dict):
            raise StateCorruptedError(
                f"State entry for '{file_id}' must be a JSON object: {path}"
            )
        try:
            result[file_id] = StateEntry(
                local_path=entry["local_path"],
                content_signature=entry["content_signature"],
                last_exported_at=entry["last_exported_at"],
                body_hash=entry["body_hash"],
                archived_at=entry.get("archived_at"),
                images=entry.get("images", {}),
            )
        except KeyError as exc:
            raise StateCorruptedError(
                f"State entry for '{file_id}' missing field {exc}: {path}"
            ) from exc
    return result


def save_state(path: Path, state: dict[str, StateEntry]) -> None:
    """Write *state* to *path* as canonical sorted JSON (indent=2, ensure_ascii=False).

    Writes to a sibling ``.tmp`` file first, then atomically replaces *path* via
    ``os.replace`` — safe against process crashes mid-write on POSIX and Windows.

    Invariant: round-trip load(save(s)) == s for any valid state.
    """
    serializable = {file_id: asdict(entry) for file_id, entry in state.items()}
    content = json.dumps(serializable, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, path)
