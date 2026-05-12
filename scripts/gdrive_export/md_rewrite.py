"""Markdown image reference rewriter for Google Docs exported Markdown.

AC-4: Replaces inline image refs (lh*.googleusercontent.com URLs) with
      local sidecar paths using positional remapping (OQ-2 resolution).
AC-6: Subset of Markdown constructs handled without degradation; unsupported
      constructs logged once per source_id per run.

OQ-2 resolution: The Drive files.export('text/markdown') renders each
inlineObject as an image tag with an ephemeral googleusercontent.com URL.
The n-th image tag in the exported Markdown corresponds to the n-th
objectId encountered in the Docs API body.content traversal (paragraph
element order). This positional mapping is used here.

No Drive API imports — complies with ING-013 conftest firewall.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from typing import Literal

# Pattern matching any inline image in Markdown: ![alt](url)
# Captures: group 1 = alt text, group 2 = URL or href
_IMAGE_INLINE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]*)\)")

# Constructs outside the AC-6 subset that are conserved verbatim.
# Detected for logging purposes only; the line is passed through unchanged.
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_NESTED_LIST_RE = re.compile(r"^ {4,}[-*+]|^\t[-*+]")
_FOOTNOTE_RE = re.compile(r"^\[\^")
_FENCED_CODE_RE = re.compile(r"^```")
_BLOCKQUOTE_RE = re.compile(r"^>")

# Map construct label → detection regex (used for fallback warning, once per source_id).
_UNSUPPORTED_CONSTRUCTS: list[tuple[str, re.Pattern[str]]] = [
    ("table", _TABLE_ROW_RE),
    ("nested_list", _NESTED_LIST_RE),
    ("footnote", _FOOTNOTE_RE),
    ("fenced_code", _FENCED_CODE_RE),
    ("blockquote", _BLOCKQUOTE_RE),
]


@dataclass
class ImageResolution:
    """Resolution outcome for one inline image objectId."""

    kind: Literal["ok", "failed", "oversized"]
    object_id: str
    rel_path: str | None
    alt: str | None


def rewrite_image_refs(
    body_md: str,
    ordered_object_ids: list[str],
    resolutions: dict[str, ImageResolution],
    source_id: str,
) -> str:
    """Rewrite inline image references in *body_md* using positional object mapping.

    Each ![alt](url) occurrence is replaced in order with the corresponding
    resolution from *resolutions* (keyed by objectId, positioned by
    *ordered_object_ids*).

    Images with no corresponding objectId (positional overflow) are passed through
    verbatim. Unsupported Markdown constructs are passed through with a one-time
    warning log per construct per source_id.

    Returns the rewritten Markdown body.
    """
    warned_constructs: set[str] = set()
    return _rewrite_body_stateful(body_md, ordered_object_ids, resolutions, source_id,
                                  warned_constructs)


def _rewrite_body_stateful(
    body_md: str,
    ordered_object_ids: list[str],
    resolutions: dict[str, ImageResolution],
    source_id: str,
    warned_constructs: set[str],
) -> str:
    """Single-pass rewrite using a stateful image counter."""
    image_position = 0

    def _replace_one(match: re.Match[str]) -> str:
        nonlocal image_position
        alt = match.group(1)
        pos = image_position
        image_position += 1

        if pos >= len(ordered_object_ids):
            # No objectId for this image — pass through verbatim.
            return match.group(0)

        obj_id = ordered_object_ids[pos]
        resolution = resolutions.get(obj_id)
        if resolution is None:
            return match.group(0)

        effective_alt = alt or (resolution.alt or "")
        if resolution.kind == "ok" and resolution.rel_path is not None:
            return f"![{effective_alt}]({resolution.rel_path})"
        if resolution.kind == "oversized":
            return f"![{effective_alt or 'image trop volumineuse'}](#oversized-{obj_id})"
        # kind == "failed"
        return f"![{effective_alt or 'image indisponible'}](#image-failed-{obj_id})"

    result = _IMAGE_INLINE_RE.sub(_replace_one, body_md)

    for line in body_md.splitlines():
        _warn_unsupported_constructs(line + "\n", source_id, warned_constructs)

    return result


def _warn_unsupported_constructs(
    line: str,
    source_id: str,
    warned: set[str],
) -> str:
    """Emit a one-time warning log for unsupported Markdown constructs (AC-6)."""
    for label, pattern in _UNSUPPORTED_CONSTRUCTS:
        if label not in warned and pattern.search(line):
            warned.add(label)
            _log({
                "event": "gdrive_sync.md_subset_fallback",
                "source_id": source_id,
                "construct": label,
            })
    return line


def _log(data: dict[str, object]) -> None:
    print(json.dumps(data), file=sys.stdout)  # noqa: T201
