"""YAML frontmatter merge for local Markdown files vs Drive-managed metadata."""

from __future__ import annotations

from typing import Any

import yaml

# Keys written by the sync script — always overwritten on re-export.
SCRIPT_MANAGED_KEYS: frozenset[str] = frozenset({
    "title",
    "source",
    "source_id",
    "drive_path",
    "exported_at",
    "archived",
    "archived_at",
})

# Keys owned by the user — created with defaults on first export, preserved on re-export.
USER_MANAGED_DEFAULTS: dict[str, Any] = {
    "tags": [],
    "access_tier": "public",
}


class FrontmatterMergeError(Exception):
    """Raised when the existing frontmatter YAML cannot be parsed."""


def merge_frontmatter(
    existing_yaml: str | None,
    script_managed: dict[str, Any],
    defaults_user: dict[str, Any],
) -> str:
    """Merge Drive metadata into local YAML frontmatter.

    - ``script_managed`` keys are always overwritten.
    - ``defaults_user`` keys are preserved if already present; otherwise set to defaults.
    - Unknown custom keys are preserved intact.
    - Raises ``FrontmatterMergeError`` if *existing_yaml* is not valid YAML.

    Returns the merged YAML string (alphabetically sorted, safe_dump).
    """
    existing: dict[str, Any] = _parse_existing(existing_yaml)
    merged = dict(existing)
    merged.update(script_managed)
    for key, default in defaults_user.items():
        if key not in merged:
            merged[key] = default
    return yaml.safe_dump(merged, default_flow_style=False, allow_unicode=True, sort_keys=True)


def _parse_existing(existing_yaml: str | None) -> dict[str, Any]:
    """Parse *existing_yaml*; return ``{}`` when *None*; raise on invalid YAML."""
    if existing_yaml is None:
        return {}
    try:
        parsed = yaml.safe_load(existing_yaml)
    except yaml.YAMLError as exc:
        raise FrontmatterMergeError(f"Invalid YAML frontmatter: {exc}") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise FrontmatterMergeError(
            f"YAML frontmatter root must be a mapping, got {type(parsed).__name__}"
        )
    return dict(parsed)
