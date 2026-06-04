"""Download all objects from a GCS bucket into a local destination directory.

Entrypoint: ``python -m archiviste_workers.ingest.download_corpus``
Reads INGEST_ROOT and LORE_CORPUS_BUCKET from environment (both required).
Returns 0 always (empty bucket = 0 objects, no-op idempotent — AC-9).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from google.cloud import storage


def _safe_target(dest_root: Path, blob_name: str) -> Path:
    """Resolve *blob_name* under *dest_root*, rejecting path traversal.

    GCS object names may contain ``..`` or absolute segments; a poisoned
    name must never write outside *dest_root* (security.md §"filenames
    never user-controlled").  Raises ``ValueError`` on escape.
    """
    target = (dest_root / blob_name).resolve()
    if target != dest_root and dest_root not in target.parents:
        raise ValueError(f"unsafe blob name: {blob_name!r}")
    return target


def download_corpus(
    client: storage.Client,
    bucket_name: str,
    dest: Path,
) -> int:
    """Download all blobs from *bucket_name* into *dest*.

    Creates *dest* if it does not exist.  Returns the number of objects
    downloaded.  An empty bucket yields 0 with no error (AC-9).
    """
    dest.mkdir(parents=True, exist_ok=True)
    dest_root = dest.resolve()
    bucket = client.bucket(bucket_name)
    count = 0
    for blob in bucket.list_blobs():
        target = _safe_target(dest_root, blob.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(target))
        count += 1
    return count


def main() -> int:
    """Read env, build client, download bucket to <ingest_root>/lore/, exit 0."""
    ingest_root = Path(os.environ["INGEST_ROOT"])
    bucket_name = os.environ["LORE_CORPUS_BUCKET"]
    dest = ingest_root / "lore"
    client = storage.Client()
    download_corpus(client, bucket_name, dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
