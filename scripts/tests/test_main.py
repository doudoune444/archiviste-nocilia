"""Tests for gdrive_export.__main__ — AC-12/13/19 (argparse, exit code, dry-run)."""

from __future__ import annotations

import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gdrive_export.__main__ import main
from gdrive_export.sync import SummaryCounts

_CREDS_PATCH = "gdrive_export.__main__.load_service_account_credentials"
_CLIENT_PATCH = "gdrive_export.__main__.DriveClient"
_SYNC_PATCH = "gdrive_export.__main__.run_sync"
_STATE_PATCH = "gdrive_export.__main__._state_path"
_LORE_PATCH = "gdrive_export.__main__._lore_root"


def _run_main_with_patches(
    tmp_path: Path,
    summary: SummaryCounts,
    argv: list[str],
    mock_run_sync: MagicMock | None = None,
) -> pytest.ExceptionInfo[SystemExit]:
    """Run main() with all infra patches applied; return the SystemExit info."""
    run_sync_mock = mock_run_sync or MagicMock(return_value=summary)
    with ExitStack() as stack:
        stack.enter_context(patch.object(sys, "argv", argv))
        stack.enter_context(patch(_CREDS_PATCH, return_value=MagicMock()))
        stack.enter_context(patch(_CLIENT_PATCH, return_value=MagicMock()))
        stack.enter_context(patch(_SYNC_PATCH, run_sync_mock))
        stack.enter_context(patch(_STATE_PATCH, return_value=tmp_path / "state.json"))
        stack.enter_context(patch(_LORE_PATCH, return_value=tmp_path / "lore"))
        with pytest.raises(SystemExit) as exc_info:
            main()
    return exc_info


class TestArgparse:
    """Argparse validation — required args."""

    def test_missing_root_folder_id_exits_nonzero(self) -> None:
        # AC-1: --root-folder-id is required; missing → exit != 0.
        with patch.object(sys, "argv", ["gdrive_export"]), pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0

    def test_dry_run_flag_accepted(self, tmp_path: Path) -> None:
        # AC-12: --dry-run accepted without error.
        exc = _run_main_with_patches(
            tmp_path,
            SummaryCounts(total=0, errors=0),
            ["gdrive_export", "--root-folder-id", "root-id", "--dry-run"],
        )
        assert exc.value.code == 0


class TestExitCode:
    """AC-19: exit code 0 on no errors, 1 on errors."""

    def test_exit_0_on_no_errors(self, tmp_path: Path) -> None:
        # AC-19: all files OK → exit 0.
        exc = _run_main_with_patches(
            tmp_path,
            SummaryCounts(total=2, created=2, errors=0),
            ["gdrive_export", "--root-folder-id", "root-id"],
        )
        assert exc.value.code == 0

    def test_exit_1_on_errors(self, tmp_path: Path) -> None:
        # AC-19: errors >= 1 → exit 1.
        exc = _run_main_with_patches(
            tmp_path,
            SummaryCounts(total=2, created=1, errors=1),
            ["gdrive_export", "--root-folder-id", "root-id"],
        )
        assert exc.value.code == 1

    def test_dry_run_exit_0_even_with_errors(self, tmp_path: Path) -> None:
        # AC-12: dry-run → exit 0 regardless of errors.
        exc = _run_main_with_patches(
            tmp_path,
            SummaryCounts(total=2, errors=2),
            ["gdrive_export", "--root-folder-id", "root-id", "--dry-run"],
        )
        assert exc.value.code == 0

    def test_run_sync_called_with_dry_run_true(self, tmp_path: Path) -> None:
        # AC-12: --dry-run passes dry_run=True to run_sync.
        mock_run_sync = MagicMock(return_value=SummaryCounts(total=0, errors=0))
        _run_main_with_patches(
            tmp_path,
            SummaryCounts(total=0, errors=0),
            ["gdrive_export", "--root-folder-id", "my-folder", "--dry-run"],
            mock_run_sync,
        )
        assert mock_run_sync.call_args.kwargs.get("dry_run") is True

    def test_root_folder_id_passed_to_run_sync(self, tmp_path: Path) -> None:
        # AC-1: --root-folder-id value is forwarded to run_sync.
        mock_run_sync = MagicMock(return_value=SummaryCounts(total=0, errors=0))
        _run_main_with_patches(
            tmp_path,
            SummaryCounts(total=0, errors=0),
            ["gdrive_export", "--root-folder-id", "specific-folder-id"],
            mock_run_sync,
        )
        assert mock_run_sync.called
        assert "specific-folder-id" in str(mock_run_sync.call_args)
