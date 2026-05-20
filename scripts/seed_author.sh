#!/usr/bin/env bash
# seed_author.sh — generate the author account SQL INSERT (SEC-001 AC-15).
#
# Usage (local, never run in CI):
#   ./scripts/seed_author.sh
#
# Outputs an SQL statement to stdout that the HUMAN must review and execute
# manually against Cloud SQL using gcloud sql connect / psql.
#
# This script NEVER commits the hash or the password. Rule: secret-hygiene.md.
#
# Prerequisites:
#   - Python 3.12+ with 'argon2-cffi' installed (pip install argon2-cffi).
#   - Run from repo root.
#
# AC-15 constraints enforced by migration (not this script):
#   - Only 'author' tier can be inserted via this seed; the application enforces
#     that signup always creates 'member' tier.
#   - No endpoint promotes a user to 'author' at runtime.

set -euo pipefail

echo "=== Archiviste author seed generator ==="
echo "This script prints SQL only. You must execute it manually."
echo ""

read -rsp "Author email: " AUTHOR_EMAIL
echo
read -rsp "Author password (min 12 chars): " AUTHOR_PASSWORD
echo
echo ""

# Validate minimum password length locally (AC-1 / AC-15)
if [ "${#AUTHOR_PASSWORD}" -lt 12 ]; then
    echo "ERROR: password must be at least 12 characters." >&2
    exit 1
fi

# Compute argon2id hash (m=19456 KiB, t=2, p=1 — matches AC-1 parameters).
HASH=$(python3 - <<PYEOF
from argon2 import PasswordHasher
ph = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16)
print(ph.hash("${AUTHOR_PASSWORD}"))
PYEOF
)

EMAIL_LOWER=$(echo "$AUTHOR_EMAIL" | tr '[:upper:]' '[:lower:]' | xargs)

echo "--- Copy and execute the following SQL against Cloud SQL (psql / gcloud sql connect): ---"
echo ""
echo "INSERT INTO users (id, email, password_hash, tier)"
echo "VALUES (gen_random_uuid(), '${EMAIL_LOWER}', '${HASH}', 'author')"
echo "ON CONFLICT DO NOTHING;"
echo ""
echo "--- Verify with: SELECT id, tier, email FROM users WHERE tier = 'author'; ---"
echo ""
echo "WARNING: the hash above is sensitive. Do NOT commit this output or store it in files."
# Clear variables from shell memory (best-effort; shell history not cleared)
unset AUTHOR_PASSWORD HASH
