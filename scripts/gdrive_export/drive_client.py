"""Google Drive API v3 wrapper.

Provides list_folder_recursive, export_gdoc_markdown, and download_png.
Timeout: 30s per HTTP call (AC-18 / security.md A04).
Drive API imports authorized here by conftest.py firewall (ING-013).
"""

from __future__ import annotations

from typing import Any

import google_auth_httplib2
import httplib2
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

_GDOC_MIME = "application/vnd.google-apps.document"
_PNG_MIME = "image/png"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_SUPPORTED_MIMES = frozenset({_GDOC_MIME, _PNG_MIME, _FOLDER_MIME})

_LIST_FIELDS = "nextPageToken, files(id, name, mimeType, parents, md5Checksum)"
_PAGE_SIZE = 1000


class DriveApiError(Exception):
    """Raised when the Drive API returns a non-success HTTP status."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class DriveClient:
    """Thin wrapper around the Drive v3 API service (read-only)."""

    def __init__(self, credentials: Credentials) -> None:
        http = httplib2.Http(timeout=30)
        authorized_http = google_auth_httplib2.AuthorizedHttp(credentials, http)
        self._service = build("drive", "v3", http=authorized_http)

    def list_folder_recursive(
        self, folder_id: str, _path_components: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Return all gdoc + PNG files under *folder_id* recursively.

        Each file dict is augmented with 'drive_path_components' (list of folder
        name strings from the root, not including the root itself).
        """
        if _path_components is None:
            _path_components = []

        result: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            response = self._list_page(folder_id, page_token)
            files: list[dict[str, Any]] = response.get("files", [])
            for file in files:
                mime = file.get("mimeType", "")
                if mime == _FOLDER_MIME:
                    sub_components = [*_path_components, file["name"]]
                    result.extend(self.list_folder_recursive(file["id"], sub_components))
                elif mime in (_GDOC_MIME, _PNG_MIME):
                    file["drive_path_components"] = list(_path_components)
                    result.append(file)
                # Other types (shortcuts, PDFs, etc.) are silently skipped per spec.
            page_token = response.get("nextPageToken")
            if page_token is None:
                break

        return result

    def export_gdoc_markdown(self, file_id: str) -> str:
        """Export a Google Doc as Markdown text.

        Raises DriveApiError on HTTP error (AC-15/16).
        """
        try:
            data: bytes = (
                self._service.files()
                .export_media(fileId=file_id, mimeType="text/markdown")
                .execute()
            )
        except HttpError as exc:
            raise DriveApiError(
                f"drive_export_failed: {exc.status_code}", exc.status_code
            ) from exc
        return data.decode("utf-8")

    def download_png(self, file_id: str) -> bytes:
        """Download a native PNG file as raw bytes.

        Raises DriveApiError on HTTP error (AC-15/16).
        """
        try:
            data: bytes = (
                self._service.files()
                .get_media(fileId=file_id)
                .execute()
            )
        except HttpError as exc:
            raise DriveApiError(
                f"drive_export_failed: {exc.status_code}", exc.status_code
            ) from exc
        return data

    def _list_page(self, folder_id: str, page_token: str | None) -> dict[str, Any]:
        """Fetch one page of files in *folder_id*."""
        kwargs: dict[str, Any] = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": _LIST_FIELDS,
            "pageSize": _PAGE_SIZE,
        }
        if page_token is not None:
            kwargs["pageToken"] = page_token
        response: dict[str, Any] = self._service.files().list(**kwargs).execute()
        return response
