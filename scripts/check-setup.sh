#!/usr/bin/env bash
# Standalone setup integrity check. Run manually or in CI (pre-commit hook).
# Exits non-zero on first failure. Verbose by default.

set -u
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"
fail=0

say() { printf "[check-setup] %s\n" "$*"; }
err() { printf "[check-setup] FAIL: %s\n" "$*" >&2; fail=1; }

say "1. CLAUDE.md size"
if [ -f CLAUDE.md ]; then
  lines=$(wc -l < CLAUDE.md)
  if [ "$lines" -gt 150 ]; then
    err "CLAUDE.md = $lines lignes, cap = 150"
  else
    say "   ok ($lines lignes)"
  fi
else
  err "CLAUDE.md missing"
fi

say "2. settings.json valid JSON"
if [ -f .claude/settings.json ]; then
  if python -c "import json; json.load(open('.claude/settings.json'))" 2>/dev/null; then
    say "   ok"
  else
    err ".claude/settings.json invalid JSON"
  fi
else
  err ".claude/settings.json missing"
fi

say "3. Agents frontmatter"
for agent in .claude/agents/*.md; do
  [ -f "$agent" ] || continue
  for key in name tools model; do
    if ! head -10 "$agent" | grep -q "^${key}:"; then
      err "$agent missing frontmatter: $key"
    fi
  done
done
[ "$fail" -eq 0 ] && say "   ok"

say "4. Rules present"
for rule in clean-code no-workaround secret-hygiene security; do
  if [ ! -f ".claude/rules/$rule.md" ]; then
    err ".claude/rules/$rule.md missing"
  fi
done
[ "$fail" -eq 0 ] && say "   ok"

say "5. No tracked secrets"
if git ls-files 2>/dev/null | grep -E '(^|/)\.env$|\.key$|\.pem$|^secrets/' | grep -v '\.env\.example$'; then
  err "secret-looking files tracked in git"
fi

say "6. .gitignore covers secrets"
if [ -f .gitignore ]; then
  for pat in '^\.env$' 'settings\.local\.json' '\*-sa\.json' '\*\.tfvars' 'kubeconfig' 'id_rsa' '\.pypirc'; do
    grep -qE "$pat" .gitignore || err ".gitignore missing pattern: $pat"
  done
fi

say "7. Lock files committed (supply chain integrity)"
if [ -f gateway/Cargo.toml ]; then
  if [ ! -f gateway/Cargo.lock ]; then
    err "gateway/Cargo.lock missing — run 'cargo build' to generate, then commit"
  elif ! git ls-files --error-unmatch gateway/Cargo.lock >/dev/null 2>&1; then
    err "gateway/Cargo.lock not tracked by git"
  else
    say "   gateway/Cargo.lock ok"
  fi
fi
if [ -f workers/pyproject.toml ]; then
  if [ ! -f workers/uv.lock ]; then
    err "workers/uv.lock missing — run 'uv lock' to generate, then commit"
  elif ! git ls-files --error-unmatch workers/uv.lock >/dev/null 2>&1; then
    err "workers/uv.lock not tracked by git"
  else
    say "   workers/uv.lock ok"
  fi
fi

say "8. deny.toml + audit configs"
if [ -f gateway/Cargo.toml ] && [ ! -f deny.toml ]; then
  err "deny.toml missing (cargo-deny config)"
fi

say "9. Guard scripts present"
for sh in guard-git guard-secret-paths format-on-save validate-claude-config; do
  if [ ! -f ".claude/scripts/${sh}.sh" ]; then
    err ".claude/scripts/${sh}.sh missing"
  fi
done

say "10. threat-model.md exists"
if [ ! -f specs/threat-model.md ]; then
  err "specs/threat-model.md missing — STRIDE threat model is required"
fi

if [ "$fail" -eq 0 ]; then
  say "ALL OK"
  exit 0
else
  exit 1
fi
