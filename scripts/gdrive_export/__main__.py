"""CLI entrypoint: python -m gdrive_export --root-folder-id <id> [--dry-run].

AC-12: --dry-run flag → exit 0 always.
AC-19: exit code 0 if errors == 0, else 1 (unless dry-run).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gdrive_export.auth import load_service_account_credentials
from gdrive_export.drive_client import DriveClient
from gdrive_export.sync import run_sync


def _state_path() -> Path:
    """Return the path to the state file (sibling of this package's directory)."""
    return Path(__file__).parent.parent / ".gdrive_state.json"


def _lore_root() -> Path:
    """Return the lore root path (two levels up from this package)."""
    return Path(__file__).parent.parent.parent / "lore"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m gdrive_export",
        description="Sync Google Drive folder to local lore/ directory.",
    )
    parser.add_argument(
        "--root-folder-id",
        required=True,
        help="Google Drive folder ID to sync recursively.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simulate sync without writing any files.",
    )
    return parser.parse_args()


def main() -> None:
    """Main entrypoint. Exits with code 0 (no errors) or 1 (errors)."""
    args = _parse_args()
    creds = load_service_account_credentials()
    client = DriveClient(creds)

    # AC-15: fail-fast if SA lacks spreadsheets.readonly / presentations.readonly.
    client.verify_extra_scopes()

    summary = run_sync(
        client,
        root_folder_id=args.root_folder_id,
        lore_root=_lore_root(),
        state_path=_state_path(),
        dry_run=args.dry_run,
    )

    if args.dry_run:
        sys.exit(0)

    sys.exit(0 if summary.errors == 0 else 1)


if __name__ == "__main__":
    main()
