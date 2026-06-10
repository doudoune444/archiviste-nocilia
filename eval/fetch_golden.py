"""Download a single GCS object to a local destination file.

CLI: python -m eval.fetch_golden --bucket BUCKET --object OBJECT --dest DEST [--timeout N]

Used by the Cloud Run eval Job to fetch the golden Q/A set from GCS before
running the Ragas eval runner (eval_job.tf command, step 1 of 2).
A04: timeout defaults to 30 s; GCS errors propagate → non-zero exit (no swallow).
A09: logs bucket/object/dest only — never logs credentials.
"""

from __future__ import annotations

import argparse

import structlog
from google.cloud import storage

log = structlog.get_logger()

DEFAULT_TIMEOUT_SECONDS = 30


def fetch_golden(
    bucket: str,
    object_path: str,
    dest: str,
    timeout: int,
) -> None:
    """Download *object_path* from *bucket* to *dest*.

    Raises google.cloud.exceptions.GoogleCloudError (or any IO error) on failure —
    callers receive a non-zero exit code (no-workaround: never swallow GCS errors).
    """
    log.info("fetch_golden.start", bucket=bucket, object=object_path, dest=dest, timeout=timeout)
    client = storage.Client()
    client.bucket(bucket).blob(object_path).download_to_filename(dest, timeout=timeout)
    log.info("fetch_golden.done", bucket=bucket, object=object_path, dest=dest)


def main(argv: list[str] | None = None) -> int:
    """Parse args, call fetch_golden, return 0 on success."""
    parser = argparse.ArgumentParser(
        description="Download a single GCS object to a local file.",
    )
    parser.add_argument("--bucket", required=True, help="GCS bucket name")
    parser.add_argument(
        "--object", required=True, dest="object_path", help="Object path inside bucket"
    )
    parser.add_argument("--dest", required=True, help="Local destination file path")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Download timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    args = parser.parse_args(argv)
    fetch_golden(
        bucket=args.bucket,
        object_path=args.object_path,
        dest=args.dest,
        timeout=args.timeout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
