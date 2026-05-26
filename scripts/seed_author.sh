#!/usr/bin/env bash
# seed_author.sh — author-tier seed (manual one-shot, humain-authored).
#
# Plan SEC-001 PR-b mandate (specs/plans/SEC-001.md L45):
#   "agent squelette commenté: prompt password local, calcul argon2id,
#    émet SQL INSERT à exécuter manuellement — humain rédige le contenu réel".
#
# This is a deliberate skeleton. The previous CWE-94 implementation
# interpolated $AUTHOR_PASSWORD into a Python heredoc — a password
# containing `"`, `\`, `$`, or newline could break out of the Python
# string literal and execute arbitrary code in the local seed shell.
#
# Humain to implement before D-step deploy:
#   1. Prompt for AUTHOR_EMAIL + AUTHOR_PASSWORD interactively
#      (`read -r email; read -rs password`). Never via argv / env exported.
#   2. Compute argon2id hash with EXACTLY m=19456, t=2, p=1
#      via stdin (e.g. `argon2 "$salt" -id -m 14 -t 2 -p 1 <<<"$password"`)
#      or python passing the password via `os.environ`/stdin — NEVER
#      string-interpolated into source.
#   3. Emit SQL on stdout:
#        INSERT INTO users (id, email, password_hash, tier, created_at)
#        VALUES (gen_random_uuid(), LOWER('<email>'), '<hash>', 'author', NOW());
#   4. Operator pipes the SQL into psql against the prod DB manually.
#      No automatic execution by this script.
#
# AC-15 invariant: only this script (or a manual DML) creates tier='author'.
# The application never promotes a user to 'author' at runtime.
#
# References:
#   - specs/acceptance/SEC-001.md AC-15
#   - specs/plans/SEC-001.md L45 (PR-b files to touch)
#   - .claude/rules/secret-hygiene.md (no secret in argv / process listing)

set -euo pipefail

echo "scripts/seed_author.sh: stub — humain to implement (see header)." >&2
exit 64
