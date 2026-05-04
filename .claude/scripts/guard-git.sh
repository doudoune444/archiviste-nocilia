#!/usr/bin/env bash
# PreToolUse Bash hook — block destructive / policy-violating ops + metachar bypass.
# Reads tool input from $CLAUDE_TOOL_INPUT_command.
# Exit 2 + stderr message → blocks the tool call.

set -u
cmd="${CLAUDE_TOOL_INPUT_command:-}"
[ -z "$cmd" ] && exit 0

# Block shell metacharacters that enable command chaining / injection.
# Whitelist exceptions: HEREDOC commit messages and quoted strings handled by allowing
# `$(cat <<'EOF' ... EOF)` ONLY inside `git commit -m`.
is_commit_heredoc=0
if echo "$cmd" | grep -qE '^git commit (-m|--message)[[:space:]]+"\$\(cat <<'\''EOF'\''.*EOF[[:space:]]*\)"[[:space:]]*$'; then
  is_commit_heredoc=1
fi

if [ "$is_commit_heredoc" -eq 0 ]; then
  # Reject command chaining / substitution / pipe / redirect.
  if echo "$cmd" | grep -qE '(;|&&|\|\||`|\$\()'; then
    echo "BLOCKED: shell metachars forbidden (; && || \` \$()). Run commands separately." >&2
    exit 2
  fi
  # Reject pipes except for known-safe inline `| head`, `| tail`, `| wc -l`.
  if echo "$cmd" | grep -qE '\|' && ! echo "$cmd" | grep -qE '\|[[:space:]]*(head|tail|wc|cat)([[:space:]]|$)'; then
    echo "BLOCKED: pipe forbidden except | head | tail | wc | cat." >&2
    exit 2
  fi
  # Reject output redirects.
  if echo "$cmd" | grep -qE '(^|[[:space:]])(>|>>|<)([[:space:]]|/)'; then
    echo "BLOCKED: redirection forbidden." >&2
    exit 2
  fi
fi

# Destructive git ops (also enforced by user global rule).
if echo "$cmd" | grep -qE 'git (checkout|switch|stash|reset --hard|push --force|push --force-with-lease|push -f|rebase|filter-branch|filter-repo)\b'; then
  echo "BLOCKED: destructive git op forbidden." >&2
  exit 2
fi

# git config / remote mutation forbidden.
if echo "$cmd" | grep -qE 'git (config|remote (add|remove|set-url|rename))\b'; then
  echo "BLOCKED: git config/remote mutation forbidden." >&2
  exit 2
fi

# Direct push to main forbidden — PR-only flow (trunk-based: feature -> main via PR).
if echo "$cmd" | grep -qE 'git push.*\borigin\s+(main|master)\b'; then
  echo "BLOCKED: direct push to main forbidden — open a PR instead." >&2
  exit 2
fi

# Block GCP metadata service exfiltration (IMDS / metadata.google.internal).
if echo "$cmd" | grep -qE '169\.254\.169\.254|metadata\.google\.internal'; then
  echo "BLOCKED: cloud metadata service access forbidden (SSRF / SA token exfil risk)." >&2
  exit 2
fi

# Block --no-verify / --no-gpg-sign hook bypass.
if echo "$cmd" | grep -qE '\-\-no-verify\b|\-\-no-gpg-sign\b'; then
  echo "BLOCKED: hook/sign bypass forbidden (--no-verify / --no-gpg-sign)." >&2
  exit 2
fi

exit 0
