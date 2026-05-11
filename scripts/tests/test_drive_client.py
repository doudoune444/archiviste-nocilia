"""Integration tests for gdrive_export.drive_client — AC-1/2/3/18 via mock service."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from gdrive_export.drive_client import DriveApiError, DriveClient

# ---------------------------------------------------------------------------
# MIME type constants
# ---------------------------------------------------------------------------

_FOLDER_MIME = "application/vnd.google-apps.folder"
_DOC_MIME = "application/vnd.google-apps.document"
_PNG_MIME = "image/png"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_client(mock_service: MagicMock) -> DriveClient:
    """Return a DriveClient backed by a mock googleapiclient service."""
    with patch("gdrive_export.drive_client.build") as mock_build:
        mock_build.return_value = mock_service
        return DriveClient(MagicMock())


def _fake_resp(status: int) -> Any:
    """Build a minimal fake HTTP response with a given status code."""
    resp = MagicMock()
    resp.status = status
    resp.reason = "Error"
    return resp


def _make_service(
    folder_pages: dict[str, list[list[dict[str, Any]]]] | None = None,
    export_body: bytes | None = None,
    export_error: Exception | None = None,
    media_body: bytes | None = None,
    media_error: Exception | None = None,
) -> MagicMock:
    """Build a mock Drive service.

    *folder_pages* maps folder_id → list of pages (each page = list of file dicts).
    Folders absent from the map return empty results.
    """
    if folder_pages is None:
        folder_pages = {}

    service = MagicMock()

    # Build per-folder page sequences.
    page_data: dict[str, list[tuple[list[dict[str, Any]], str | None]]] = {}
    for fid, pages in folder_pages.items():
        page_data[fid] = []
        for i, page in enumerate(pages):
            token = f"tok-{fid}-{i + 1}" if i < len(pages) - 1 else None
            page_data[fid].append((page, token))

    call_counts: dict[str, int] = {}

    def _list_side_effect(**kwargs: Any) -> MagicMock:
        q: str = kwargs.get("q", "")
        # Extract folder_id from query like: 'FOLDER_ID' in parents ...
        folder_id = "__unknown__"
        if "'" in q:
            parts = q.split("'")
            if len(parts) >= 2:
                folder_id = parts[1]

        idx = call_counts.get(folder_id, 0)
        call_counts[folder_id] = idx + 1

        if folder_id in page_data and idx < len(page_data[folder_id]):
            items, next_tok = page_data[folder_id][idx]
        else:
            items, next_tok = [], None

        response: dict[str, Any] = {"files": items}
        if next_tok is not None:
            response["nextPageToken"] = next_tok

        mock_req = MagicMock()
        mock_req.execute.return_value = response
        return mock_req

    service.files.return_value.list.side_effect = _list_side_effect

    if export_error is not None:
        service.files.return_value.export_media.return_value.execute.side_effect = export_error
    elif export_body is not None:
        service.files.return_value.export_media.return_value.execute.return_value = export_body

    if media_error is not None:
        service.files.return_value.get_media.return_value.execute.side_effect = media_error
    elif media_body is not None:
        service.files.return_value.get_media.return_value.execute.return_value = media_body

    return service


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListFolderRecursive:
    """AC-1: list_folder_recursive returns gdoc + png files from folder hierarchy."""

    def test_empty_folder(self) -> None:
        # AC-1: empty folder returns empty list.
        client = _build_client(_make_service(folder_pages={"root": []}))
        assert client.list_folder_recursive("root") == []

    def test_single_page_gdoc_and_png(self) -> None:
        # AC-1: returns gdoc + png files, skips folder entries from list.
        gdoc = {"id": "doc1", "name": "Doc One", "mimeType": _DOC_MIME}
        png = {"id": "png1", "name": "image.png", "mimeType": _PNG_MIME}
        folder = {"id": "sub1", "name": "Sub", "mimeType": _FOLDER_MIME}
        shortcut = {"id": "sc1", "name": "sc", "mimeType": "application/vnd.google-apps.shortcut"}

        client = _build_client(
            _make_service(folder_pages={"root": [[gdoc, png, folder, shortcut]], "sub1": []})
        )
        result = client.list_folder_recursive("root")
        ids = [f["id"] for f in result]
        assert "doc1" in ids
        assert "png1" in ids
        assert "sub1" not in ids
        assert "sc1" not in ids

    def test_pagination_followed(self) -> None:
        # AC-1: follows nextPageToken across multiple pages.
        page1 = [{"id": "doc1", "name": "D1", "mimeType": _DOC_MIME}]
        page2 = [{"id": "doc2", "name": "D2", "mimeType": _DOC_MIME}]
        client = _build_client(_make_service(folder_pages={"root": [page1, page2]}))
        result = client.list_folder_recursive("root")
        assert len(result) == 2
        assert {f["id"] for f in result} == {"doc1", "doc2"}

    def test_recursive_subfolder(self) -> None:
        # AC-1: recurses into subfolders, returning docs from nested folders.
        subfolder = {"id": "sub1", "name": "SubFolder", "mimeType": _FOLDER_MIME}
        doc_in_sub = {"id": "doc1", "name": "NestedDoc", "mimeType": _DOC_MIME}
        client = _build_client(
            _make_service(folder_pages={"root": [[subfolder]], "sub1": [[doc_in_sub]]})
        )
        result = client.list_folder_recursive("root")
        assert len(result) == 1
        assert result[0]["id"] == "doc1"

    def test_drive_path_components_root_level(self) -> None:
        # AC-2: file at root level has empty drive_path_components.
        doc = {"id": "doc1", "name": "Doc", "mimeType": _DOC_MIME}
        client = _build_client(_make_service(folder_pages={"root": [[doc]]}))
        result = client.list_folder_recursive("root")
        assert result[0]["drive_path_components"] == []

    def test_drive_path_components_nested(self) -> None:
        # AC-2: file in subfolder carries subfolder name in drive_path_components.
        sub = {"id": "sub1", "name": "Chapter1", "mimeType": _FOLDER_MIME}
        doc = {"id": "doc1", "name": "Doc", "mimeType": _DOC_MIME}
        client = _build_client(
            _make_service(folder_pages={"root": [[sub]], "sub1": [[doc]]})
        )
        result = client.list_folder_recursive("root")
        assert result[0]["drive_path_components"] == ["Chapter1"]


class TestExportGdocMarkdown:
    """AC-2: export_gdoc_markdown returns the markdown string."""

    def test_returns_markdown_string(self) -> None:
        # AC-2: exports gdoc as markdown text/markdown.
        expected = "# Hello\n\nWorld\n"
        client = _build_client(_make_service(export_body=expected.encode()))
        assert client.export_gdoc_markdown("doc-id") == expected

    def test_raises_drive_api_error_on_5xx(self) -> None:
        # AC-16: HTTP 5xx → raises DriveApiError.
        client = _build_client(
            _make_service(export_error=HttpError(resp=_fake_resp(503), content=b""))
        )
        with pytest.raises(DriveApiError):
            client.export_gdoc_markdown("doc-id")

    def test_raises_drive_api_error_on_429(self) -> None:
        # AC-15: 429 quota → DriveApiError with status_code 429.
        client = _build_client(
            _make_service(export_error=HttpError(resp=_fake_resp(429), content=b""))
        )
        with pytest.raises(DriveApiError) as exc_info:
            client.export_gdoc_markdown("doc-id")
        assert exc_info.value.status_code == 429


class TestDownloadPng:
    """AC-3: download_png returns raw bytes."""

    def test_returns_bytes(self) -> None:
        # AC-3: downloads PNG as bytes via files.get_media.
        expected = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        client = _build_client(_make_service(media_body=expected))
        assert client.download_png("png-id") == expected

    def test_raises_drive_api_error_on_5xx(self) -> None:
        # AC-16: 5xx on download → raises DriveApiError.
        client = _build_client(
            _make_service(media_error=HttpError(resp=_fake_resp(503), content=b""))
        )
        with pytest.raises(DriveApiError):
            client.download_png("png-id")


class TestTimeout:
    """AC-18: httplib2.Http(timeout=30) is called at construction."""

    def test_http_timeout_30s(self) -> None:
        # AC-18: timeout=30 passed to httplib2.Http constructor.
        with patch("gdrive_export.drive_client.httplib2") as mock_httplib2:
            mock_httplib2.Http.return_value = MagicMock()
            with patch("gdrive_export.drive_client.build"):
                DriveClient(MagicMock())
            mock_httplib2.Http.assert_called_once_with(timeout=30)
