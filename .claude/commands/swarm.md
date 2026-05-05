---
description: Parallel multi-ticket execution via worktrees. Plan+impl parallel, review+ship serial.
argument-hint: <ID-A> <ID-B> ...
---

L'utilisateur veut exécuter `$ARGUMENTS` (IDs espace-séparés, **ordre = dépendance merge**) en parallèle.

Stateless comme `/cycle` : ré-invocable, lit file-system + `gh` à chaque appel.

## Phase 0 — Bootstrap

1. IDs match `^[A-Z]+-[0-9]+$`. Sinon abort en listant invalides.
2. Pour chaque ID : `specs/acceptance/<ID>.md` existe avec `## Status` → `ready`. Manquants/draft → abort, demande `/spec <ID>`.
3. `git fetch origin`.
4. `bash .claude/scripts/swarm-init.sh <IDs...>` — crée `.worktrees/<ID>/` depuis `origin/main` si absent (idempotent).

## Phase A — PLAN + IMPL parallèles

Détection par ID (file-system) :
- `PLAN` : `specs/plans/<ID>.md` absent
- `IMPL` : plan présent, `git -C .worktrees/<ID>/ log origin/main..HEAD` vide

Pour tous IDs en `PLAN`/`IMPL` : **spawn N Agent en un seul message** (subagent_type = `architect` puis `implementer`). Prompt par agent :

> Tu opères dans `.worktrees/<ID>/` (Agent tool n'a pas de cwd) :
> - Git : `git -C .worktrees/<ID>/ <op>`. Jamais `checkout/switch/stash/rebase/reset/-f`.
> - Read/Edit/Write/Glob/Grep : paths absolus sous `<repo-root>/.worktrees/<ID>/`.
> - Cargo : `cargo --manifest-path <repo-root>/.worktrees/<ID>/gateway/Cargo.toml ...`.
> - Uv : `uv run --directory <repo-root>/.worktrees/<ID>/workers ...`.
> - Pas de `cd`, `&&`, `;`, pipes (sauf `| head|tail|wc|cat`).
>
> Si plan absent : `/plan <ID>` (pre-flight obligatoire). Sinon : `/impl <ID>`. STOP à chaque `git commit` pour OK humain (commit-validation rule).
>
> Stop quand impl committé. Reporte au coordinateur (phase finale + résumé commits).

## Phase A.5 — Conflits ship

Pour chaque paire (X, Y) : intersection de `git -C .worktrees/<X>/ diff --name-only origin/main` ∩ Y avec `{CHANGELOG.md, Cargo.lock, uv.lock, gateway/Cargo.toml, workers/pyproject.toml}`. Overlap → préviens humain : ship strictement sériel + ff-only entre chaque.

## Phase B — REVIEW + SHIP sériels

Itère `$ARGUMENTS` en ordre. Par ID :

1. Review absente → délègue à `reviewer` dans worktree (mêmes conventions cwd-less).
2. `BLOCK`/`REQUEST_CHANGES` → reporte findings HIGH, **abort la chaîne** (IDs suivants attendent un nouveau `/swarm`).
3. `APPROVE` + pas de PR → `/ship <ID>` dans worktree.
4. STOP, dis humain : `Merge PR #<num> pour <ID>, puis relance /swarm <IDs restants>`. Ne polle jamais.

## Phase C — Cleanup post-merge

À la ré-invocation, par ID dont `gh pr list --head feat/<ID>-* --state merged` non vide :

1. `/cleanup <ID>` (depuis main repo).
2. Pour worktrees restants : `git -C .worktrees/<next>/ pull --ff-only origin main`. Conflit → reporte humain.
