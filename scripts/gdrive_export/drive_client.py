"""Google Drive API v3 wrapper.

Provides list_folder_recursive, export_gdoc_markdown, download_png,
get_spreadsheet_tabs, get_sheet_values, get_presentation, verify_extra_scopes.
Timeout: 30s per HTTP call (AC-18 / security.md A04).
Drive API imports authorized here by conftest.py firewall (ING-013).
"""

from __future__ import annotations

import json
import sys
from typing import Any

import google_auth_httplib2
import httplib2
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

_GDOC_MIME = "application/vnd.google-apps.document"
_PNG_MIME = "image/png"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_SLIDES_MIME = "application/vnd.google-apps.presentation"
_SUPPORTED_MIMES = frozenset({_GDOC_MIME, _PNG_MIME, _FOLDER_MIME, _SHEET_MIME, _SLIDES_MIME})

_LIST_FIELDS = "nextPageToken, files(id, name, mimeType, parents, md5Checksum)"
_PAGE_SIZE = 1000
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404


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
        self._sheets_service = build("sheets", "v4", http=authorized_http)
        self._slides_service = build("slides", "v1", http=authorized_http)

    @classmethod
    def from_services(
        cls,
        drive_service: Any,
        sheets_service: Any,
        slides_service: Any,
    ) -> DriveClient:
        """Construct a DriveClient from pre-built API service objects.

        LOW-7: test-only factory that avoids accessing private attributes directly.
        """
        instance = object.__new__(cls)
        instance._service = drive_service
        instance._sheets_service = sheets_service
        instance._slides_service = slides_service
        return instance

    def list_folder_recursive(
        self, folder_id: str, _path_components: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Return all gdoc + PNG + gsheet + gslide files under *folder_id* recursively.

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
                elif mime in (_GDOC_MIME, _PNG_MIME, _SHEET_MIME, _SLIDES_MIME):
                    file["drive_path_components"] = list(_path_components)
                    result.append(file)
                # Other types (shortcuts, PDFs, etc.) are silently skipped per spec.
            page_token = response.get("nextPageToken")
            if page_token is None:
                break

        return result

    def get_spreadsheet_tabs(self, file_id: str) -> list[dict[str, Any]]:
        """Return sheet metadata list from Sheets API v4 spreadsheets.get.

        Each entry has 'title', 'sheetId' (gid), 'index'.
        Raises DriveApiError on HTTP error.
        """
        try:
            resp: dict[str, Any] = (
                self._sheets_service.spreadsheets()
                .get(spreadsheetId=file_id, fields="sheets(properties(title,sheetId,index))")
                .execute()
            )
        except HttpError as exc:
            raise DriveApiError(
                f"sheets_get_failed: {exc.status_code}", exc.status_code
            ) from exc
        sheets: list[dict[str, Any]] = resp.get("sheets", [])
        return [s["properties"] for s in sheets]

    def get_sheet_values(self, file_id: str, tab_title: str) -> list[list[str]]:
        """Return cell values for *tab_title* via Sheets API v4 spreadsheets.values.get.

        Returns a list of rows, each row a list of string cell values.
        Raises DriveApiError on HTTP error.
        """
        try:
            resp: dict[str, Any] = (
                self._sheets_service.spreadsheets()
                .values()
                .get(spreadsheetId=file_id, range=tab_title)
                .execute()
            )
        except HttpError as exc:
            raise DriveApiError(
                f"sheets_values_get_failed: {exc.status_code}", exc.status_code
            ) from exc
        rows: list[list[str]] = resp.get("values", [])
        return rows

    def get_presentation(self, file_id: str) -> dict[str, Any]:
        """Return presentation data from Slides API v1 presentations.get.

        Raises DriveApiError on HTTP error.
        """
        try:
            resp: dict[str, Any] = (
                self._slides_service.presentations()
                .get(presentationId=file_id)
                .execute()
            )
        except HttpError as exc:
            raise DriveApiError(
                f"slides_get_failed: {exc.status_code}", exc.status_code
            ) from exc
        return resp

    def verify_extra_scopes(self) -> None:
        """AC-15: fail-fast if SA lacks spreadsheets.readonly or presentations.readonly.

        Probes Sheets API with a bogus ID; expects 404 (found scope, no such file)
        or raises SystemExit on 403 insufficient scope.
        LOW-3: unexpected HTTP codes (502, network errors) are re-raised so boot
        fail-fast triggers visibly rather than silently passing scope validation.
        """
        try:
            self._sheets_service.spreadsheets().get(spreadsheetId="__probe__").execute()
        except HttpError as exc:
            if exc.status_code == _HTTP_FORBIDDEN and _is_insufficient_scope(exc):
                msg = (
                    "gdrive scope missing: spreadsheets.readonly and "
                    "presentations.readonly required"
                )
                print(msg, file=sys.stderr)  # noqa: T201
                sys.exit(1)
            if exc.status_code == _HTTP_NOT_FOUND:
                # 404 = probe success (scope OK, spreadsheet not found — expected)
                return
            # Unexpected HTTP error (5xx, network) — re-raise so boot fails visibly.
            raise DriveApiError(
                f"scope_probe_failed: {exc.status_code}", exc.status_code
            ) from exc

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


def _is_insufficient_scope(exc: HttpError) -> bool:
    """Return True if HttpError indicates a missing OAuth scope (AC-15).

    Parses the error body JSON for 'insufficientPermissions' or 'accessNotConfigured'.
    On parse failure, returns True (fail-fast is the safer default).
    """
    try:
        content = exc.content if isinstance(exc.content, bytes) else exc.content.encode()
        body: dict[str, Any] = json.loads(content)
        errors: list[dict[str, Any]] = body.get("error", {}).get("errors", [])
        return any(
            e.get("reason") in ("insufficientPermissions", "accessNotConfigured")
            for e in errors
        )
    except (json.JSONDecodeError, KeyError, AttributeError, UnicodeDecodeError):
        # Parse failure → fail-fast is the safer default (LOW-2: specific exceptions only)
        return True
