"""Parse YAML frontmatter delimited by `---` lines at the head of a Markdown file."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

import yaml

ALLOWED_ACCESS_TIERS: Final = frozenset({"public", "members", "author_only"})
DEFAULT_ACCESS_TIER: Final = "public"

_FRONTMATTER_PATTERN: Final = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.DOTALL)


class FrontmatterError(ValueError):
    """Raised when frontmatter is missing, malformed, or fails validation."""


@dataclass(frozen=True, slots=True)
class Frontmatter:
    """Validated frontmatter for an ingested document."""

    title: str
    tags: list[str] = field(default_factory=list)
    access_tier: str = DEFAULT_ACCESS_TIER


def parse_frontmatter(raw: str) -> tuple[Frontmatter, str]:
    """Split raw markdown into (Frontmatter, body). Raise FrontmatterError on invalid input."""
    match = _FRONTMATTER_PATTERN.match(raw)
    if match is None:
        msg = "missing frontmatter delimiters"
        raise FrontmatterError(msg)
    yaml_block, body = match.group(1), match.group(2)
    try:
        data = yaml.safe_load(yaml_block)
    except yaml.YAMLError as exc:
        msg = f"invalid frontmatter YAML: {exc}"
        raise FrontmatterError(msg) from exc
    if not isinstance(data, dict):
        msg = "frontmatter must be a YAML mapping"
        raise FrontmatterError(msg)
    return _build_frontmatter(data), body


def _build_frontmatter(data: dict[str, object]) -> Frontmatter:
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        msg = "title is required and must be a non-empty string"
        raise FrontmatterError(msg)
    tags_raw = data.get("tags", [])
    if not isinstance(tags_raw, list) or not all(isinstance(item, str) for item in tags_raw):
        msg = "tags must be a list of strings"
        raise FrontmatterError(msg)
    access_tier = data.get("access_tier", DEFAULT_ACCESS_TIER)
    if access_tier not in ALLOWED_ACCESS_TIERS:
        msg = f"access_tier must be one of {sorted(ALLOWED_ACCESS_TIERS)}"
        raise FrontmatterError(msg)
    return Frontmatter(title=title, tags=list(tags_raw), access_tier=access_tier)
