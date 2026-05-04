#!/usr/bin/env bash
# PreToolUse Edit|Write hook — runs ONLY when target path matches .claude/** or CLAUDE.md.
# Validates invariants right after a sensitive config edit. Cheap, ciblé.
# Exit 2 + stderr → blocks the tool call so the model retries / surfaces issue.

set -u
f="${CLAUDE_FILE_PATH:-}"
[ -z "$f" ] && exit 0

# Only fire on .claude/** or CLAUDE.md edits.
case "$f" in
  *.claude/*|*CLAUDE.md) ;;
  *) exit 0 ;;
esac

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo .)"
fail=0

# 1. CLAUDE.md ≤ 150 lignes (flexible cap).
if [ -f "$REPO_ROOT/CLAUDE.md" ]; then
  lines=$(wc -l < "$REPO_ROOT/CLAUDE.md")
  if [ "$lines" -gt 150 ]; then
    echo "WARN: CLAUDE.md = $lines lignes (cap 150). Trim before commit." >&2
  fi
fi

# 2. settings.json must be valid JSON.
if [ -f "$REPO_ROOT/.claude/settings.json" ]; then
  if ! python -c "import json,sys; json.load(open(r'$REPO_ROOT/.claude/settings.json'))" 2>/dev/null; then
    echo "BLOCKED: .claude/settings.json invalid JSON" >&2
    fail=1
  fi
fi

# 3. Agents must have valid frontmatter (name, tools, model).
for agent in "$REPO_ROOT/.claude/agents/"*.md; do
  [ -f "$agent" ] || continue
  if ! head -10 "$agent" | grep -q '^name:' || \
     ! head -10 "$agent" | grep -q '^tools:' || \
     ! head -10 "$agent" | grep -q '^model:'; then
    echo "BLOCKED: $agent missing frontmatter (name/tools/model)" >&2
    fail=1
  fi
done

[ "$fail" -eq 1 ] && exit 2
exit 0
