#!/usr/bin/env bash
# PreToolUse Bash hook — block destructive / policy-violating git ops + metachar bypass.
# Reads tool input from $CLAUDE_TOOL_INPUT_command.
# Exit 2 + stderr message → blocks the tool call.

set -u
cmd="${CLAUDE_TOOL_INPUT_command:-}"
[ -z "$cmd" ] && exit 0

# ---- Special case: `git commit` ---------------------------------------------
# Commit messages are inherently free-form (heredocs, $(...), pipes inside
# message body). Generic metachar/redirect checks would false-match on body
# content. Apply only commit-specific bans + branch guard.
if echo "$cmd" | grep -qE '^[[:space:]]*git[[:space:]]+commit\b'; then
  if echo "$cmd" | grep -qE '\-\-no-verify\b|\-\-no-gpg-sign\b'; then
    echo "BLOCKED: hook/sign bypass forbidden (--no-verify / --no-gpg-sign)." >&2
    exit 2
  fi
  branch=$(git branch --show-current 2>/dev/null || echo "")
  case "$branch" in
    main|master|develop)
      echo "BLOCKED: cannot commit on '$branch' — open a feature branch first." >&2
      exit 2
      ;;
  esac
  exit 0
fi

# ---- Special case: `git push` -----------------------------------------------
# Block direct push from main/master via current-branch check (cwd-aware,
# avoids regex false-match on commit-message bodies containing "git push origin main").
if echo "$cmd" | grep -qE '^[[:space:]]*git[[:space:]]+push\b'; then
  if echo "$cmd" | grep -qE '\-\-force\b|\-\-force-with-lease\b|[[:space:]]-f\b|\-\-no-verify\b'; then
    echo "BLOCKED: force-push / hook bypass forbidden." >&2
    exit 2
  fi
  branch=$(git branch --show-current 2>/dev/null || echo "")
  case "$branch" in
    main|master)
      echo "BLOCKED: direct push from '$branch' forbidden — open a PR instead." >&2
      exit 2
      ;;
  esac
  exit 0
fi

# ---- Generic metachar / redirect / pipe block (non-commit, non-push) --------
if echo "$cmd" | grep -qE '(;|&&|\|\||`|\$\()'; then
  echo "BLOCKED: shell metachars forbidden (; && || \` \$()). Run commands separately." >&2
  exit 2
fi
if echo "$cmd" | grep -qE '\|' && ! echo "$cmd" | grep -qE '\|[[:space:]]*(head|tail|wc|cat)([[:space:]]|$)'; then
  echo "BLOCKED: pipe forbidden except | head | tail | wc | cat." >&2
  exit 2
fi
if echo "$cmd" | grep -qE '(^|[[:space:]])(>|>>|<)([[:space:]]|/)'; then
  echo "BLOCKED: redirection forbidden." >&2
  exit 2
fi

# ---- Destructive git ops ----------------------------------------------------
if echo "$cmd" | grep -qE 'git (reset --hard|rebase|filter-branch|filter-repo)\b'; then
  echo "BLOCKED: destructive git op forbidden (history rewrite / data loss)." >&2
  exit 2
fi

# git config / remote mutation forbidden.
if echo "$cmd" | grep -qE 'git (config|remote (add|remove|set-url|rename))\b'; then
  echo "BLOCKED: git config/remote mutation forbidden." >&2
  exit 2
fi

# Block GCP metadata service exfiltration (IMDS / metadata.google.internal).
if echo "$cmd" | grep -qE '169\.254\.169\.254|metadata\.google\.internal'; then
  echo "BLOCKED: cloud metadata service access forbidden (SSRF / SA token exfil risk)." >&2
  exit 2
fi

exit 0
