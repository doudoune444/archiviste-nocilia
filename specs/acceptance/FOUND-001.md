# FOUND-001 — Scaffolding repo minimal viable

## Contexte

Premier ticket. Le repo doit pouvoir builder gateway + workers, exposer un `/healthz` end-to-end, et passer le CI à vide. Aucune logique métier.

## Critères d'acceptation

- AC-1 : `cd gateway && cargo build` compile sans erreur ni warning.
- AC-2 : `cd workers && uv sync && uv run pytest` passe (0 test ou 1 test trivial).
- AC-3 : `docker compose up -d` lance gateway + workers + postgres + pgvector.
- AC-4 : `curl http://localhost:8080/healthz` retourne `{"status":"ok","version":"0.1.0"}`.
- AC-5 : `curl http://localhost:8080/healthz` traverse le gateway et appelle `http://workers:8000/healthz` en interne (preuve dans les logs).
- AC-6 : CI vert sur la PR (lint + build + tests).

## Non-goals

- Pas de retrieval, pas de génération, pas de schéma DB applicatif.
- Pas d'auth, pas de rate-limit.
- Pas de Langfuse / observability avancée.

## Touch points (informatif)

- `gateway/Cargo.toml`, `gateway/src/main.rs`, `gateway/src/lib.rs`
- `workers/pyproject.toml`, `workers/src/archiviste_workers/main.py`
- `docker-compose.yml`
- `.github/workflows/ci.yml`

## Test oracle

- Intégration : `gateway/tests/healthz_test.rs` lance le gateway et appelle `/healthz`, attend 200 + shape JSON.
- Property : aucune.
- Eval : aucune.

## Estimation d'effort

M

## Status

ready
