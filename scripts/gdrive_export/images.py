"""Image pipeline: extract inlineObjects, compress, persist, cleanup orphans.

AC-3/3a/3b/3c: two-stage compression (Lanczos resize + JPEG q=85 conditional).
AC-5: intra-doc dedup by MD5 of final compressed bytes.
AC-10: cleanup orphaned sidecar files after each run.
AC-12: rename sidecar directory on gdoc rename.
AC-18: MD5 regex validation + Path.resolve() safety guard.

No Drive API imports — complies with ING-013 conftest firewall.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import PIL.Image

# Security: cap pixels before resize to mitigate DecompressionBomb (Pillow CVE, AC-8 spec).
_DECOMP_BOMB_PIXELS = 2048 * 2048 * 4
PIL.Image.MAX_IMAGE_PIXELS = _DECOMP_BOMB_PIXELS

_MAX_DIM = 2048
_JPEG_QUALITY = 85
_MAX_BYTES_AFTER = 5 * 1024 * 1024  # 5 MiB
_MIME_TO_EXT: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}
_MD5_RE = re.compile(r"^[0-9a-f]{12}$")


class ImageOversizedError(Exception):
    """Raised when the image exceeds 5 MiB even after two-stage compression (AC-3c)."""

    def __init__(self, bytes_after_compression: int) -> None:
        super().__init__(f"image too large after compression: {bytes_after_compression} bytes")
        self.bytes_after_compression = bytes_after_compression


class ImageUndecodableError(Exception):
    """Raised when the payload cannot be decoded by Pillow or has an unsupported MIME."""


@dataclass
class CompressedImage:
    """Result of the two-stage compression pipeline."""

    bytes_final: bytes
    mime_final: str
    ext_final: str
    md5_hex_12: str
    log_events: list[dict[str, Any]] = field(default_factory=list)


def extract_inline_objects(doc: dict[str, Any]) -> dict[str, str]:
    """Return a map of objectId → contentUri from a Docs API document dict.

    Merges both inlineObjects and positionedObjects. Real-world Docs corpora
    place most images in positionedObjects (anchored); inlineObjects are rare.
    """
    result: dict[str, str] = {}
    _extract_from_container(doc.get("inlineObjects", {}), "inlineObjectProperties", result)
    _extract_from_container(
        doc.get("positionedObjects", {}), "positionedObjectProperties", result
    )
    return result


def _extract_from_container(
    container: dict[str, Any],
    props_key: str,
    result: dict[str, str],
) -> None:
    """Extract objectId → contentUri entries from an inlineObjects or positionedObjects dict."""
    for obj_id, obj in container.items():
        props = obj.get(props_key, {})
        embedded = props.get("embeddedObject", {})
        uri = embedded.get("imageProperties", {}).get("contentUri", "")
        if uri:
            result[obj_id] = uri


def compress_image(raw: bytes, src_mime: str, source_id: str, image_index: int) -> CompressedImage:
    """Apply the two-stage compression pipeline and return a CompressedImage.

    Stage 1 (AC-3a): Lanczos resize if max(w,h) > 2048px — always applied, idempotent.
    Stage 2 (AC-3b): JPEG re-encode if bytes > 5 MiB — conditional.
    Raises ImageOversizedError if still > 5 MiB after stage 2 (AC-3c).
    Raises ImageUndecodableError if src_mime is not in _MIME_TO_EXT or Pillow fails.
    """
    if src_mime not in _MIME_TO_EXT:
        raise ImageUndecodableError(f"unsupported content type: {src_mime}")

    log_events: list[dict[str, Any]] = []

    try:
        img_file: PIL.Image.Image = PIL.Image.open(io.BytesIO(raw))
        img_file.load()
    except Exception as exc:
        raise ImageUndecodableError(f"pillow decode failed: {exc}") from exc

    current_bytes, current_mime, image = _stage_resize(
        img_file, raw, src_mime, source_id, image_index, log_events
    )
    current_bytes, current_mime = _stage_jpeg(
        image, current_bytes, current_mime, src_mime, source_id, image_index, log_events
    )

    if len(current_bytes) > _MAX_BYTES_AFTER:
        raise ImageOversizedError(len(current_bytes))

    md5_hex_12 = hashlib.md5(current_bytes).hexdigest()[:12]  # noqa: S324
    ext_final = _MIME_TO_EXT.get(current_mime, "jpg")
    return CompressedImage(
        bytes_final=current_bytes,
        mime_final=current_mime,
        ext_final=ext_final,
        md5_hex_12=md5_hex_12,
        log_events=log_events,
    )


def _stage_resize(
    image: PIL.Image.Image,
    raw: bytes,
    src_mime: str,
    source_id: str,
    image_index: int,
    log_events: list[dict[str, Any]],
) -> tuple[bytes, str, PIL.Image.Image]:
    """Resize image if max(w,h) > _MAX_DIM; return (bytes, mime, image)."""
    width, height = image.size
    if max(width, height) <= _MAX_DIM:
        return raw, src_mime, image

    if width >= height:
        new_w, new_h = _MAX_DIM, max(1, round(_MAX_DIM * height / width))
    else:
        new_w, new_h = max(1, round(_MAX_DIM * width / height)), _MAX_DIM

    resized = image.resize((new_w, new_h), PIL.Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    fmt = image.format or "PNG"
    resized.save(buf, format=fmt)
    resized_bytes = buf.getvalue()

    log_events.append({
        "event": "gdrive_sync.image_resized",
        "source_id": source_id,
        "image_index": image_index,
        "width_before": width,
        "height_before": height,
        "width_after": new_w,
        "height_after": new_h,
    })
    return resized_bytes, src_mime, resized


def _stage_jpeg(
    image: PIL.Image.Image,
    current_bytes: bytes,
    current_mime: str,
    src_mime: str,
    source_id: str,
    image_index: int,
    log_events: list[dict[str, Any]],
) -> tuple[bytes, str]:
    """Re-encode to JPEG q=85 if current_bytes > _MAX_BYTES_AFTER; return (bytes, mime)."""
    if len(current_bytes) <= _MAX_BYTES_AFTER:
        return current_bytes, current_mime

    rgb_image = image.convert("RGB")
    buf = io.BytesIO()
    rgb_image.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    jpeg_bytes = buf.getvalue()

    log_events.append({
        "event": "gdrive_sync.image_recompressed",
        "source_id": source_id,
        "image_index": image_index,
        "src_mime": src_mime,
        "dst_mime": "image/jpeg",
        "bytes_before": len(current_bytes),
        "bytes_after": len(jpeg_bytes),
    })
    return jpeg_bytes, "image/jpeg"


def compute_image_path(
    lore_root: Path,
    drive_components: list[str],
    doc_slug: str,
    md5_hex: str,
    ext: str,
) -> Path:
    """Return the absolute sidecar path for an image, with safety guards (AC-18).

    Raises ValueError if md5_hex does not match ^[0-9a-f]{12}$.
    Raises ValueError if resolved path is not under lore_root.
    """
    if not _MD5_RE.match(md5_hex):
        raise ValueError(f"invalid md5_hex: {md5_hex!r} — must match ^[0-9a-f]{{12}}$")

    sidecar_dir = lore_root.joinpath(*drive_components) / f"{doc_slug}.images"
    image_path = sidecar_dir / f"{md5_hex}.{ext}"

    resolved = image_path.resolve()
    lore_resolved = lore_root.resolve()
    if not resolved.is_relative_to(lore_resolved):
        raise ValueError(f"image path escapes lore_root: {resolved}")

    return image_path


def cleanup_orphans(
    sidecar_dir: Path,
    kept_md5s: set[str],
    source_id: str,
    *,
    dry_run: bool,
    lore_root: Path | None = None,
) -> int:
    """Remove files in sidecar_dir whose MD5 prefix is not in kept_md5s.

    Returns count of removed files. Removes the directory itself if it becomes empty.
    Emits log events per removal (AC-10).

    AC-18 / defense-in-depth: if lore_root is supplied, verifies that sidecar_dir
    stays under lore_root before touching the filesystem (path-traversal guard).
    """
    if lore_root is not None:
        resolved = sidecar_dir.resolve()
        if not resolved.is_relative_to(lore_root.resolve()):
            raise ValueError(
                f"cleanup_orphans: sidecar_dir escapes lore_root: {resolved}"
            )

    if not sidecar_dir.exists():
        return 0

    removed = 0
    for child in list(sidecar_dir.iterdir()):
        prefix = child.name.split(".")[0]
        if prefix not in kept_md5s:
            if not dry_run:
                child.unlink()
            _log({"event": "gdrive_sync.image_orphan_removed",
                  "path": str(child), "source_id": source_id})
            removed += 1

    if not dry_run and sidecar_dir.exists() and not any(sidecar_dir.iterdir()):
        sidecar_dir.rmdir()

    return removed


def rename_sidecar(old_md_path: Path, new_md_path: Path, source_id: str) -> None:
    """Rename the sidecar directory when the .md is renamed (AC-12).

    Emits log event on rename. No-op if old sidecar does not exist.
    """
    old_dir = old_md_path.parent / f"{old_md_path.stem}.images"
    new_dir = new_md_path.parent / f"{new_md_path.stem}.images"

    if not old_dir.exists():
        return

    new_dir.parent.mkdir(parents=True, exist_ok=True)
    old_dir.rename(new_dir)
    _log({
        "event": "gdrive_sync.images_dir_renamed",
        "source_id": source_id,
        "old": str(old_dir),
        "new": str(new_dir),
    })


def _log(data: dict[str, Any]) -> None:
    print(json.dumps(data), file=sys.stdout)  # noqa: T201
