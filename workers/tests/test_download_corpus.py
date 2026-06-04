"""Unit tests for download_corpus module (AC-5, AC-9)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archiviste_workers.ingest import download_corpus as _module


# AC-5: blobs written under dest/
def test_download_corpus_writes_blobs(tmp_path: Path) -> None:
    """AC-5: ≥1 blob in bucket → each file written under dest/."""
    dest = tmp_path / "lore"

    blob_a = MagicMock()
    blob_a.name = "chapter1.md"
    blob_b = MagicMock()
    blob_b.name = "sub/chapter2.md"

    mock_bucket = MagicMock()
    mock_bucket.list_blobs.return_value = [blob_a, blob_b]

    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    def fake_download_to_filename(path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("content")

    blob_a.download_to_filename.side_effect = fake_download_to_filename
    blob_b.download_to_filename.side_effect = fake_download_to_filename

    count = _module.download_corpus(mock_client, "my-bucket", dest)

    assert count == 2
    assert (dest / "chapter1.md").exists()
    assert (dest / "sub" / "chapter2.md").exists()
    mock_client.bucket.assert_called_once_with("my-bucket")


# Security: path traversal in blob name must be rejected (security.md §filenames)
def test_download_corpus_rejects_path_traversal(tmp_path: Path) -> None:
    """A blob name escaping dest/ raises ValueError, writes nothing outside."""
    dest = tmp_path / "lore"

    evil_blob = MagicMock()
    evil_blob.name = "../../etc/pwned.md"

    mock_bucket = MagicMock()
    mock_bucket.list_blobs.return_value = [evil_blob]

    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    with pytest.raises(ValueError, match="unsafe blob name"):
        _module.download_corpus(mock_client, "poisoned-bucket", dest)

    evil_blob.download_to_filename.assert_not_called()


# AC-9: empty bucket → 0 objects, dest dir created, no error
def test_download_corpus_empty_bucket_returns_zero(tmp_path: Path) -> None:
    """AC-9: empty bucket → count 0, dest dir created, no exception."""
    dest = tmp_path / "lore"

    mock_bucket = MagicMock()
    mock_bucket.list_blobs.return_value = []

    mock_client = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    count = _module.download_corpus(mock_client, "empty-bucket", dest)

    assert count == 0
    assert dest.is_dir()


# AC-9: main() exits 0 on empty bucket
def test_main_exits_zero_on_empty_bucket(tmp_path: Path) -> None:
    """AC-9: main() reads env, downloads 0 blobs, exits 0."""
    env = {
        "INGEST_ROOT": str(tmp_path),
        "LORE_CORPUS_BUCKET": "test-corpus-bucket",
    }

    mock_client_instance = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.list_blobs.return_value = []
    mock_client_instance.bucket.return_value = mock_bucket

    with (
        patch.dict(os.environ, env, clear=False),
        patch("archiviste_workers.ingest.download_corpus.storage") as mock_storage,
    ):
        mock_storage.Client.return_value = mock_client_instance
        exit_code = _module.main()

    assert exit_code == 0


# AC-5: main() exits 0 after downloading blobs
def test_main_exits_zero_with_blobs(tmp_path: Path) -> None:
    """AC-5: main() downloads ≥1 blob, exits 0."""
    env = {
        "INGEST_ROOT": str(tmp_path),
        "LORE_CORPUS_BUCKET": "test-corpus-bucket",
    }

    blob = MagicMock()
    blob.name = "lore.md"

    def fake_download(path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("body")

    blob.download_to_filename.side_effect = fake_download

    mock_client_instance = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.list_blobs.return_value = [blob]
    mock_client_instance.bucket.return_value = mock_bucket

    with (
        patch.dict(os.environ, env, clear=False),
        patch("archiviste_workers.ingest.download_corpus.storage") as mock_storage,
    ):
        mock_storage.Client.return_value = mock_client_instance
        exit_code = _module.main()

    assert exit_code == 0
