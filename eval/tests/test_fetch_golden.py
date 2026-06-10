"""Unit tests for eval/fetch_golden.py — OBS-009 fix: GCS fetch extracted from inline python -c."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


# AC: Client().bucket() called with bucket arg, .blob() with object arg,
# .download_to_filename() called with (dest, timeout=<value>).
def test_fetch_golden_calls_gcs_correctly(tmp_path: Path) -> None:
    """fetch_golden invokes GCS client with the given bucket, object, dest, and timeout."""
    dest = str(tmp_path / "golden_qa.jsonl")

    mock_blob = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_client_instance = MagicMock()
    mock_client_instance.bucket.return_value = mock_bucket

    with patch("eval.fetch_golden.storage") as mock_storage:
        mock_storage.Client.return_value = mock_client_instance
        from eval.fetch_golden import fetch_golden
        fetch_golden(bucket="my-bucket", object_path="golden/golden_qa.jsonl", dest=dest, timeout=15)

    mock_storage.Client.assert_called_once_with()
    mock_client_instance.bucket.assert_called_once_with("my-bucket")
    mock_bucket.blob.assert_called_once_with("golden/golden_qa.jsonl")
    mock_blob.download_to_filename.assert_called_once_with(dest, timeout=15)


# AC: default timeout is 30 s (A04: external call timeout).
def test_fetch_golden_default_timeout(tmp_path: Path) -> None:
    """main() with no --timeout flag uses the 30 s default."""
    dest = str(tmp_path / "golden_qa.jsonl")

    mock_blob = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_client_instance = MagicMock()
    mock_client_instance.bucket.return_value = mock_bucket

    with patch("eval.fetch_golden.storage") as mock_storage:
        mock_storage.Client.return_value = mock_client_instance
        from eval.fetch_golden import main
        exit_code = main(["--bucket", "my-bucket", "--object", "golden/golden_qa.jsonl", "--dest", dest])

    assert exit_code == 0
    mock_blob.download_to_filename.assert_called_once_with(dest, timeout=30)


# AC: explicit --timeout overrides the default.
def test_fetch_golden_custom_timeout(tmp_path: Path) -> None:
    """main() with --timeout 60 passes 60 to download_to_filename."""
    dest = str(tmp_path / "golden_qa.jsonl")

    mock_blob = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_client_instance = MagicMock()
    mock_client_instance.bucket.return_value = mock_bucket

    with patch("eval.fetch_golden.storage") as mock_storage:
        mock_storage.Client.return_value = mock_client_instance
        from eval.fetch_golden import main
        exit_code = main(["--bucket", "b", "--object", "o", "--dest", dest, "--timeout", "60"])

    assert exit_code == 0
    mock_blob.download_to_filename.assert_called_once_with(dest, timeout=60)


# AC: GCS errors propagate (no swallow → non-zero exit via raised exception).
def test_fetch_golden_propagates_gcs_error(tmp_path: Path) -> None:
    """fetch_golden lets GCS errors propagate; main() exits non-zero via SystemExit."""
    import pytest
    dest = str(tmp_path / "golden_qa.jsonl")

    mock_blob = MagicMock()
    mock_blob.download_to_filename.side_effect = RuntimeError("GCS network error")
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_client_instance = MagicMock()
    mock_client_instance.bucket.return_value = mock_bucket

    with patch("eval.fetch_golden.storage") as mock_storage:
        mock_storage.Client.return_value = mock_client_instance
        from eval.fetch_golden import fetch_golden
        with pytest.raises(RuntimeError, match="GCS network error"):
            fetch_golden(bucket="b", object_path="o", dest=dest, timeout=30)
