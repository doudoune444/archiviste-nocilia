#!/usr/bin/env bash
# PreToolUse Write hook — block writing to paths that look like secret artifacts.
# Reads target path from $CLAUDE_TOOL_INPUT_file_path.
# Exit 2 + stderr → blocks the tool call.

set -u
path="${CLAUDE_TOOL_INPUT_file_path:-}"
[ -z "$path" ] && exit 0

# Normalize to lowercase for matching (Windows path-insensitive).
lc=$(echo "$path" | tr '[:upper:]' '[:lower:]')

forbidden_patterns=(
  '\.env$'
  '\.env\.[^/]+$'
  '\.key$'
  '\.pem$'
  '\.p12$'
  '\.pfx$'
  '/id_rsa$'
  '/id_ed25519$'
  '-sa\.json$'
  'service-account[^/]*\.json$'
  '\.tfvars$'
  'terraform\.tfstate$'
  'terraform\.tfstate\.backup$'
  '/kubeconfig'
  '/\.npmrc$'
  '/\.pypirc$'
  '/secrets/'
)

for pat in "${forbidden_patterns[@]}"; do
  if echo "$lc" | grep -qE "$pat"; then
    echo "BLOCKED: writing to secret-shaped path forbidden: $path" >&2
    echo "If legitimate (e.g. example file), use *.example suffix and commit explicitly." >&2
    exit 2
  fi
done

exit 0
