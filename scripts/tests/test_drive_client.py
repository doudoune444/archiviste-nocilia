"""Integration tests for gdrive_export.drive_client — AC-1/2/3/18 via mock service."""

from __future__ import annotations

import json
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


class TestSheetsApi:
    """AC-1 ING-011: get_spreadsheet_tabs and get_sheet_values."""

    def _build_sheets_client(self, mock_sheets_svc: MagicMock) -> DriveClient:
        with patch("gdrive_export.drive_client.build") as mock_build:
            mock_build.return_value = MagicMock()
            client = DriveClient(MagicMock())
            client._sheets_service = mock_sheets_svc
            return client

    def test_get_spreadsheet_tabs_returns_properties(self) -> None:
        # AC-1: returns list of {title, sheetId, index} from API response
        svc = MagicMock()
        svc.spreadsheets.return_value.get.return_value.execute.return_value = {
            "sheets": [
                {"properties": {"title": "Tab1", "sheetId": 0, "index": 0}},
                {"properties": {"title": "Tab2", "sheetId": 42, "index": 1}},
            ]
        }
        client = self._build_sheets_client(svc)
        tabs = client.get_spreadsheet_tabs("file123")
        assert len(tabs) == 2
        assert tabs[0]["title"] == "Tab1"
        assert tabs[1]["sheetId"] == 42

    def test_get_sheet_values_returns_rows(self) -> None:
        # AC-2: returns list of rows (list of list of str)
        svc = MagicMock()
        svc.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [["H1", "H2"], ["v1", "v2"]]
        }
        client = self._build_sheets_client(svc)
        rows = client.get_sheet_values("file123", "Tab1")
        assert rows == [["H1", "H2"], ["v1", "v2"]]

    def test_get_spreadsheet_tabs_raises_on_http_error(self) -> None:
        # AC-15: HttpError → DriveApiError
        svc = MagicMock()
        svc.spreadsheets.return_value.get.return_value.execute.side_effect = (
            HttpError(resp=_fake_resp(403), content=b'{"error":{"errors":[]}}')
        )
        client = self._build_sheets_client(svc)
        with pytest.raises(DriveApiError):
            client.get_spreadsheet_tabs("file123")


class TestSlidesApi:
    """AC-9 ING-011: get_presentation."""

    def test_get_presentation_returns_dict(self) -> None:
        # AC-9: returns presentation dict from Slides API
        svc = MagicMock()
        svc.spreadsheets = MagicMock()
        slides_svc = MagicMock()
        pres_data: dict[str, Any] = {"slides": [{"pageElements": []}]}
        slides_svc.presentations.return_value.get.return_value.execute.return_value = pres_data

        with patch("gdrive_export.drive_client.build") as mock_build:
            mock_build.return_value = MagicMock()
            client = DriveClient(MagicMock())
            client._slides_service = slides_svc

        result = client.get_presentation("pres123")
        assert result == pres_data

    def test_get_presentation_raises_on_http_error(self) -> None:
        # AC-9: HttpError → DriveApiError
        slides_svc = MagicMock()
        slides_svc.presentations.return_value.get.return_value.execute.side_effect = (
            HttpError(resp=_fake_resp(503), content=b"")
        )
        with patch("gdrive_export.drive_client.build") as mock_build:
            mock_build.return_value = MagicMock()
            client = DriveClient(MagicMock())
            client._slides_service = slides_svc

        with pytest.raises(DriveApiError):
            client.get_presentation("pres123")


class TestScopeProbe:
    """AC-15 ING-011: verify_extra_scopes exits on 403 insufficient scope."""

    def test_404_probe_success(self) -> None:
        # AC-15: 404 = scope present, spreadsheet not found (expected)
        sheets_svc = MagicMock()
        sheets_svc.spreadsheets.return_value.get.return_value.execute.side_effect = (
            HttpError(resp=_fake_resp(404), content=b"")
        )
        with patch("gdrive_export.drive_client.build") as mock_build:
            mock_build.return_value = MagicMock()
            client = DriveClient(MagicMock())
            client._sheets_service = sheets_svc

        # Should not raise or exit
        client.verify_extra_scopes()

    def test_403_insufficient_scope_exits(self, capsys: Any) -> None:
        # AC-15: 403 with reason insufficientPermissions → sys.exit(1)
        sheets_svc = MagicMock()
        sheets_svc.spreadsheets.return_value.get.return_value.execute.side_effect = (
            HttpError(
                resp=_fake_resp(403),
                content=json.dumps({
                    "error": {"errors": [{"reason": "insufficientPermissions"}]}
                }).encode(),
            )
        )
        with patch("gdrive_export.drive_client.build") as mock_build:
            mock_build.return_value = MagicMock()
            client = DriveClient(MagicMock())
            client._sheets_service = sheets_svc

        with pytest.raises(SystemExit) as exc_info:
            client.verify_extra_scopes()
        assert exc_info.value.code == 1
        assert "gdrive scope missing" in capsys.readouterr().err
