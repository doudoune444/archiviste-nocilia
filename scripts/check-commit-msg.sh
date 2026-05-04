#!/usr/bin/env bash
# Conventional Commits validator (pre-commit commit-msg hook).
# Usage: check-commit-msg.sh <commit-msg-file>
set -euo pipefail

msg_file="${1:?missing commit-msg file path}"
first_line=$(head -n1 "$msg_file")

pattern='^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\(.+\))?: .+'

if ! echo "$first_line" | grep -qE "$pattern"; then
  echo "Commit message must follow Conventional Commits."
  echo "Format: <type>(<scope>): <subject>"
  echo "Types : feat fix docs style refactor perf test build ci chore revert"
  echo "Got   : $first_line"
  exit 1
fi
