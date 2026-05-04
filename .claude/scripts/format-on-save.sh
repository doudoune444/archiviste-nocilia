#!/usr/bin/env bash
# PostToolUse Edit|Write hook — format the SINGLE file just edited.
# Scope limited to one file to avoid full-crate / full-package format storms.

set -u
f="${CLAUDE_FILE_PATH:-}"
[ -z "$f" ] && exit 0
[ ! -f "$f" ] && exit 0

case "$f" in
  *.rs)
    # rustfmt direct on the one file. Loses workspace rustfmt.toml is fine —
    # CI runs `cargo fmt --check` and catches drift.
    rustfmt --edition 2021 "$f" >/dev/null 2>&1 || true
    ;;
  *.py)
    # ruff format on the single file.
    (cd "$(dirname "$f")" && uv run ruff format "$f" --quiet >/dev/null 2>&1) || true
    ;;
esac

exit 0
