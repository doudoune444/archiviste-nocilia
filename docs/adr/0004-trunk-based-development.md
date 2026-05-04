# ADR 0004 — Trunk-based development on `main`

- Status: accepted
- Date: 2026-05-04
- Decider: Doudoune
- Supersedes: implicit GitFlow (`main` ← `develop` ← `feat/*`) used during early bootstrap

## Context

The repository was bootstrapped with a GitFlow-style topology :

- `main` — release-only branch
- `develop` — default integration branch
- `feat/<ID>-slug` — short-lived feature branches targeting `develop`
- Releases via merge `develop -> main` + tag

This was carried over from larger team conventions. After shipping `FOUND-001` and reviewing the workflow against the project's actual constraints (solo developer, vertical slice ≤ 300 LOC, continuous delivery via `release-please`, agentic workflow with `/spec` → `/plan` → `/impl` → `/review` → `/ship`), GitFlow proved to be friction without payoff :

- **Two long-lived branches** for one developer doubles synchronization cost (Dependabot, branch protection, default branch confusion).
- **`develop -> main` merges** add ceremony — every release becomes a multi-step ritual instead of a tag.
- **PR target ambiguity** — agents and humans had to remember "feature → develop, hotfix → main" rules encoded in `guard-git.sh` and slash commands.
- **DORA / Accelerate research** consistently shows trunk-based + short-lived branches outperforms GitFlow on lead time and change failure rate, especially below team size 10.
- **Spec-driven + agile** workflow already enforces a per-ticket vertical slice with mandatory review gate. Trunk-based naturally fits : every approved slice merges to trunk and is releasable.

## Decision

Adopt **trunk-based development** on `main` :

- `main` is the **default branch** and the integration branch.
- Feature branches `feat/<ID>-<slug>` and `hotfix/<slug>` are short-lived (≤ a few days) and target `main` directly via PR.
- `release-please` automates `vX.Y.Z` tags and `CHANGELOG.md` from conventional commits landing on `main`.
- No `develop` branch. No `release/*` branches. No long-lived integration branch.

## Rationale

Why trunk-based fits this project :

- **Solo + agentic workflow** — `/spec` → `/plan` → `/impl` → `/review` → `/ship` already delivers a per-ticket gated vertical slice. Trunk-based removes the redundant `develop` integration step.
- **Continuous delivery primitive** — `release-please` operates on conventional commits to `main`. Adding a `develop` middleman delays semver bumps without quality benefit.
- **Smaller cognitive surface** — one default, one PR target, one set of branch protection rules.
- **Industry alignment** — GitHub Flow / trunk-based is the dominant pattern for SaaS / Cloud-Run-style continuous deployment. GitFlow is a fit for shrink-wrapped multi-version software, which this project is not.
- **Tooling alignment** — Dependabot, branch protection, CodeQL, secret scanning all simplify with a single protected default branch.

## Consequences

Positive :

- One default branch, one PR target, one protection ruleset.
- Faster lead time per ticket (no `develop -> main` merge ceremony).
- `release-please` operates directly on `main`.
- Slash commands and guard hooks lose `develop` special cases.
- Dependabot PRs target `main` (the default), no explicit `target-branch` needed.

Negative / accepted trade-offs :

- No long-running integration branch to batch unreleased work — mitigated by feature flags if a slice ever needs to land dark.
- Hotfix path now indistinguishable from feature path at the branch level — distinguished by ticket ID prefix (`hotfix/<slug>`) and PR labels only.
- Requires strict branch protection on `main` (PR review, status checks, linear history, no force push) since every push is a release candidate.

## Migration plan

1. Edit workflow docs (`CLAUDE.md`, `BOOTSTRAP.md`), slash commands (`.claude/commands/{impl,review,ship}.md`), agent prompts (`.claude/agents/{implementer,reviewer}.md`), guard hook (`.claude/scripts/guard-git.sh`), Dependabot (`.github/dependabot.yml`) to drop `develop` references and target `main`.
2. Land this ADR via `chore/migrate-trunk-based` PR to `develop` (last PR to ever target `develop`).
3. Merge remaining open Dependabot PRs to `develop`.
4. Fast-forward merge `develop -> main`.
5. GitHub: change default branch to `main`, delete `develop`, update branch protection ruleset on `main`.
6. Reopen any in-flight `feat/*` branches against `main`.

## References

- `CLAUDE.md` — branch topology line
- `.claude/scripts/guard-git.sh` — push / PR base policy
- DORA / Accelerate, _State of DevOps_ reports — trunk-based correlated with high performers
- GitHub Flow — <https://docs.github.com/en/get-started/using-github/github-flow>
