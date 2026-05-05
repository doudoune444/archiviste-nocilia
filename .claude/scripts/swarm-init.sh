#!/usr/bin/env bash
# Bootstrap worktrees for /swarm. Idempotent.
# Args: <ID-A> [<ID-B> ...]
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: swarm-init.sh <ID-A> [<ID-B> ...]" >&2
  exit 1
fi

ID_RE='^[A-Z]+-[0-9]+$'
for id in "$@"; do
  if ! [[ "$id" =~ $ID_RE ]]; then
    echo "invalid ID: $id (expected ^[A-Z]+-[0-9]+\$)" >&2
    exit 1
  fi
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

for id in "$@"; do
  wt=".worktrees/$id"
  branch="feat/$id"
  if [ ! -d "$wt" ]; then
    echo "creating worktree $wt on $branch from origin/main" >&2
    git worktree add -b "$branch" "$wt" origin/main >&2
  else
    echo "worktree $wt exists, skipping" >&2
  fi
done
