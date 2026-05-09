# ADR 0006 — `google-api-python-client` isolé sous `scripts/` pour le sync Google Drive

- Status: accepted
- Date: 2026-05-09
- Decider: humain (auteur du projet)

## Context

ING-013 livrera un outil offline / dev (`python -m gdrive_export`) qui parcourt un dossier Google Drive partagé en lecture seule à un service account, exporte les `gdoc` en `*.md` et les `image/png` sous `lore/`. Cet outil n'est invoqué ni par la `gateway/` Rust ni par les `workers/` Python en runtime (cf vision §52, §54).

Le SDK officiel `google-api-python-client` pèse > 1k LOC avec transitives non-triviales (`httplib2`, `googleapis-common-protos`, `uritemplate`, `google-auth`). Selon `.claude/rules/security.md` A06 (« New dep above 1k LOC or any FFI: requires ADR »), tout ajout de cette ampleur exige une décision architecturale documentée.

ING-010 (utilities pures) ne dépend pas du SDK ; ING-013 sera son seul consommateur.

## Decision

Accepter `google-api-python-client>=2.140` et `google-auth>=2.35` comme dépendances de `scripts/pyproject.toml` exclusivement, jamais de `workers/pyproject.toml` ni de `gateway/Cargo.toml`. Le lockfile `scripts/uv.lock` est isolé du venv `workers/`. ING-010 prépare cette isolation (lockfile séparé, package `scripts/gdrive_export/` autonome) ; ING-013 ajoutera les imports.

Scope d'usage : `roles/drive.readonly` uniquement, sur un seul dossier Drive partagé manuellement au service account. Aucun OAuth user, aucun Shared Drive, aucune écriture Drive.

## Consequences

### Easier
- **Blast radius zéro runtime** : la gateway et les workers ne chargent jamais ce SDK ; aucune surface CVE ne touche l'app de production. Audit `pip-audit` côté `workers/` reste propre.
- **Lockfile isolé** : un changement de version du SDK Drive n'impacte pas `workers/uv.lock` ni `gateway/Cargo.lock`. Permet d'avancer ING-013 sans synchroniser tous les autres tickets.
- **Outil dev uniquement** : pas d'exposition réseau, pas de surface d'attaque inbound. Déploiement Cloud Run ne package jamais `scripts/`.
- **Test isolation** : `cd scripts && uv run pytest` ne nécessite pas le venv `workers/`.

### Harder
- **Deux venvs à maintenir** : `workers/` et `scripts/` ont leurs propres `uv sync`. Onboarding humain doit documenter cette dualité (futur runbook ING-013).
- **Pas de partage de code** entre `workers/` et `scripts/` sans copie ou packaging interne (ex. si `normalize_body` doit être réutilisé côté workers ingestion, il faudra le déplacer en lib partagée — décision déférée tant qu'aucun second consommateur n'apparaît, cf clean-code.md « Hardcode until a second caller appears »).
- **CI matrix élargie** : pipeline doit lancer `uv sync` + tests sur `scripts/` séparément.

### Cost
- ~7 deps transitives ajoutées dans `scripts/uv.lock` uniquement.
- ~200ms d'import time pour le SDK Drive au boot du script (acceptable, tool offline).
- Surface d'audit `pip-audit` côté `scripts/` non-zéro mais isolée.

## Alternatives considered

- **REST manuel via `httpx`** — rejeté : implique réécrire OAuth2 service account flow + retry + pagination listing Drive. Surface code plus grande, risque sécurité plus haut (crypto JWT manuelle), maintenance lourde pour un gain illusoire (`httpx` est aussi une dep externe).
- **Skip Drive API totalement, export manuel** — rejeté : casse la vision §52 (Drive = source amont automatisée). Forcerait l'auteur à exporter à la main chaque doc, retire toute valeur du sync.
- **Ajouter le SDK Drive dans `workers/pyproject.toml`** — rejeté : pollue le venv runtime de production avec une dep dev-only ; augmente blast radius CVE inutilement ; viole le principe « least dependencies in production path ».
- **Packager `scripts/` comme sous-package de `workers/`** (`workers/src/scripts/...`) — rejeté : crée une dépendance circulaire conceptuelle (workers = runtime ; scripts = offline tool) et empêche l'isolation lockfile.

## Amendment trigger

Cet ADR doit être amendé (nouveau ADR superseding) si :
- Un second consommateur Python du SDK Drive apparaît côté `workers/` (ex. ingestion temps réel via webhook Drive).
- Le scope d'auth s'élargit au-delà de `drive.readonly` (écriture Drive, Shared Drives, OAuth user).
- Le SDK Drive est packagé / proposé comme service géré par Cloud Run (Drive activity log streaming).

## References

- `specs/acceptance/ING-010.md` — utilities lib, lockfile isolation (AC-1, AC-13).
- `specs/acceptance/ING-013.md` — Drive API integration, consommateur unique du SDK.
- `.claude/rules/security.md` A06 — règle « new dep above 1k LOC requires ADR ».
- `.claude/rules/secret-hygiene.md` — `*-sa.json` jamais commité.
- Vision §52, §54 — Drive offline sync, dev-tool boundary.
