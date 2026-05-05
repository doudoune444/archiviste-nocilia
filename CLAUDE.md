# Archiviste Nocilia

RAG public web multi-utilisateurs. Gateway Rust (Axum) + workers Python (FastAPI/LangChain).
Persistence conversations en Markdown sur GCS. Tickets lore-gap PostgreSQL.

## Stack

- **Gateway** : Rust 1.95+, Axum 0.8, Tokio, sqlx, tower (rate-limit, auth, CORS).
- **Workers** : Python 3.12+, FastAPI, LangChain, pgvector, Sentence-Transformers.
- **DB** : PostgreSQL 16 + extension `vector`.
- **Storage** : GCS bucket `archiviste-conversations`.
- **Observability** : Langfuse (traces LLM) + OpenTelemetry (metrics/logs).
- **Infra** : Cloud Run + Terraform.

## Structure

```
gateway/        # Rust Axum API, contrats REST, auth, rate-limit
workers/        # Python FastAPI : ingestion, retrieval, generation, eval
specs/          # SOURCES VÉRITÉ — acceptance/, golden_qa.jsonl, openapi/, properties.md
docs/           # architecture.md, ADR, runbook
eval/           # Ragas runner + golden set execution
infra/          # Terraform + docker-compose
```

## Workflow non négociable

1. **Spec d'abord** : `/spec <ID> "<brief>"` lance `spec-author` en Socratique. Humain auteur, agent interroge et formalise.
2. **Plan avant code** : `/plan <ID>` → `architect` produit `plan.md` validé humain. **Pre-flight obligatoire** avant `ExitPlanMode` ou plan finalisé : agent liste (a) fichiers/dirs lus, (b) 3 hypothèses clés du plan, (c) zones d'incertitude. Attend confirmation humaine avant de présenter le plan.
3. **Vertical slice ≤ 300 lignes/PR** : 1 PR = 1 ticket = end-to-end.
4. **Sub-agents séparés** : `spec-author`, `architect`, `implementer`, `reviewer`, `eval-runner`, `debugger`.
5. **Branch topology** : trunk-based — `main` ← `feat/<ID>-slug`. PR target = `main`. Tags `vX.Y.Z` gérés par release-please. Voir [`docs/adr/0004-trunk-based-development.md`](docs/adr/0004-trunk-based-development.md).
6. **Commits = agents, validation humaine** : agent prépare le diff + message de commit, **présente à l'humain et attend validation explicite** avant `git commit`. Scopes : `docs(spec):`, `docs(plan):`, `feat/fix(scope):`, `docs(review):`, `chore(eval):`. Humain ne commit pas à la main.
7. **CI gates** : `cargo fmt --check` + `cargo clippy -D warnings` + `cargo test` + `ruff check` + `mypy --strict` + `pytest` + schemathesis (si OpenAPI) + Ragas eval (si RAG).

## Conventions code

Conventions langage = linters strict (source vérité) :
- **Rust** : `gateway/Cargo.toml [lints.rust]` + `[lints.clippy]` (deny unwrap/panic/print/dbg, warn pedantic, missing_docs warn).
- **Python** : `workers/pyproject.toml [tool.ruff]` + `[tool.mypy]` (strict, disallow_untyped_defs, T20/PT/RET/PL/N).
- **Tests** : `gateway/tests/` (intégration), `workers/tests/`. Tests par vertical slice.
- **Property tests** : `proptest` (Rust), `hypothesis` (Python).

Conventions architecture = `.claude/rules/*.md` (lues par agents) :
- `clean-code.md` — SRP, naming, fn ≤40 lignes, no premature abstraction
- `vertical-slice.md` — TDD order, ≤300 LOC, migration first
- `no-workaround.md` — blocker → log + stop
- `secret-hygiene.md` — never commit secrets

## Sources de vérité (humain-only)

Ces fichiers ne sont **jamais** modifiés par un agent sans approbation humaine explicite :

- `specs/acceptance/<ID>.md` — critères d'acceptation par ticket
- `specs/golden_qa.jsonl` — set Q/A de référence pour Ragas
- `specs/properties.md` — invariants property-based
- `specs/openapi/gateway-to-workers.yml` — contrat REST
- `migrations/*.sql` — schéma DB

## Garde-fous

Hooks actifs (`.claude/scripts/`) :
- `guard-git.sh` — bloque destructive git, push direct main, PR `--base main` hors hotfix/release
- `format-on-save.sh` — rustfmt/ruff format sur fichier édité
- `validate-claude-config.sh` — valide `.claude/**` + `CLAUDE.md` ≤150 lignes

Lints transverses (`scripts/`) :
- `check-ports.sh` — enforce ports canoniques. Source de vérité = `docker-compose.yml` (gateway=8080, workers=8000, postgres=5432). Tout doc qui mentionne un port doit matcher. Wired en pre-commit + CI.

Règles humaines :
- Ne **jamais** `git checkout/switch/stash` (rule globale).
- Ne **jamais** modifier `specs/` (humain-only) sans approbation.
- Ne **jamais** désactiver un test pour passer CI — comprendre la cause.
- Ne **jamais** ajouter dépendance lourde sans ADR (`docs/adr/NNNN-*.md`).

## Commandes locales

```bash
# Gateway
cd gateway && cargo run
cd gateway && cargo test
cd gateway && cargo clippy -- -D warnings

# Workers
cd workers && uv run uvicorn archiviste_workers.main:app --reload
cd workers && uv run pytest
cd workers && uv run ruff check . && uv run mypy src/

# Eval
uv run python eval/ragas_runner.py --set specs/golden_qa.jsonl

# Stack complet
docker compose up -d
```
