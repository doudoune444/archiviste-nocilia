# Vision — Archiviste Nocilia

> Fichier = **ce qui reste à faire**. Le travail livré n'y figure pas. Mis à jour 2026-06-09.

## Pitch (1 phrase)

Chatbot RAG public sur un univers de fiction écrit par l'auteur. Le système classifie l'intent et répond selon 4 modes ; les questions sans réponse créent des tickets « lore-gap » pour guider l'enrichissement du corpus.

## Public cible

- **Lecteurs / fans** de l'univers (anonymes ou inscrits) — posent des questions, lisent les réponses.
- **Auteur** (toi) — consomme les tickets lore-gap pour combler les trous, lit les conversations pour contexte.

## Objectifs

1. **Outil utile** pour l'auteur : feedback loop sur cohérence + trous de l'univers.
2. **Trophée portfolio** : Rust (Axum) + Python (FastAPI) + RAG + GCP + sécurité publique + observabilité + load-tested.
3. **Test scaling crédible** : load tests documentés (100 / 500 users).

## Les 4 modes de réponse (produit, livré)

| Mode | Déclencheur | Réponse |
|---|---|---|
| 1 — Canon | `in_domain` + retrieval score ≥ threshold | Réponse cohérente avec lore + citations, ton in-world |
| 2 — Refus poli | `off_topic` | Refus court + suggestion de questions in-domain |
| 3 — Lore-gap | `unknown_in_domain` (in-domain mais retrieval < threshold) | Réponse "noté pour archives" + **création ticket auto** lié à la conversation |
| 4 — Mystère | `acl_blocked` (info `restricted` non visible par ce user) | Réponse mystérieuse sans révéler que l'info existe (timing identique aux autres modes) |

## Architecture (rappel synthétique)

Cf. [`architecture.md`](architecture.md) pour le diagramme complet.

- **Gateway Rust (Axum)** : public, perf-critique. Auth JWT + tier, prompt-injection filter, PII scrub, sert le frontend statique (`gateway/static/`).
- **Workers Python (FastAPI)** : internes, écosystème ML mature. Pipeline LangChain : intent → ACL → retrieve → coherence → answer + conversation_logger + ticket_service.
- **DB** : Postgres + pgvector (chunks, conversations index, tickets, query_log).
- **GCS** : `archiviste-conversations` (1 `.md` append-only par conversation).
- **Observabilité** : Langfuse (traces LLM) + OpenTelemetry (metrics/logs).

## Concepts clés

- **Conversation** : 1 session user = 1 conversation. Persisté en Markdown GCS, indexé Postgres léger. Dashboard auteur ouvre `.md` via signed URL.
- **Ticket lore-gap** : `id`, `conversation_id` (FK), `question`, `category`, `priority_score` (incrémenté si question similaire détectée par cosine ≥ 0.85), `status`. **Aucun autre type de ticket dans l'app.**
- **ACL contenu** : chaque doc lore porte `access_tier` (`public` / `members` / `author_only`). Filtrage post-retrieval.
- **User tiers** : `anonymous` (fingerprint IP+UA+cookie) / `member` (signup) / `author`.

## Stack validée (référence)

- **LLM** : LLM-agnostique via wrapper `workers/.../services/llm.py` (env `LLM_PROVIDER`/`LLM_MODEL`/`LLM_API_KEY`). MVP = Mistral (`mistral-small-latest` generation + intent, `mistral-embed` dim 1024). Swap provider = env var + redémarrage, zéro changement code. Fallback chain prévu SEC-010.
- **Vector DB** : Postgres + pgvector.
- **Frontend** : HTML+CSS+JS vanilla servi par la gateway via `tower-http::ServeDir`. Aucune build step, aucune dépendance JS tierce. Cf. [`adr/0005-frontend-vanilla-served-by-gateway.md`](adr/0005-frontend-vanilla-served-by-gateway.md).
- **Compte Claude Max** : dev uniquement (claude.ai, Claude Code CLI), **pas programmatiquement**. Workers utilisent une API key séparée du provider.

## SLOs cibles

- Disponibilité : 99,0 % mensuel.
- Latence : p95 chat round-trip < 3 s.
- Eval Ragas : faithfulness ≥ 0,85, answer_relevancy ≥ 0,85 sur golden set.
- Overhead Rust gateway : < 80 ms p95 à 500 users concurrents.

## Non-goals (toujours valides)

- Pas de multi-univers / multi-tenant (1 univers, 1 auteur).
- Pas de mémoire conversationnelle multi-turn complexe.
- Pas de génération illustrations (img2img).
- Pas de fine-tuning LoRA dédié.
- Pas de mobile app native.
- Pas de gRPC interne (HTTP/JSON suffit).

## État actuel (V1 live)

Application **déployée et fonctionnelle** sur `https://archiviste.nocilia.fr` : chat public end-to-end (4 modes), persistence conversations GCS, tickets lore-gap, dashboard auteur, auth JWT (anonymous/member/author), corpus privé GCS ingéré.

Infra en place : Cloud Run (gateway + workers, scale-to-zero, europe-west9) · Cloud SQL Postgres 16 + pgvector · GCS `archiviste-conversations` (lifecycle 30j) · Secret Manager · Cloudflare (DNS/TLS/rate-limit 100 req/min/IP) · GHA deploy WIF + canary + smoke + auto-rollback · Terraform IaC.

---

## Ce qui reste à faire

### EPIC OBS — Observabilité publique

But : page publique `/observability` (anonyme) exposant la santé du système. **OBS-001 → 004 livrés** (nav header, `GET /v1/stats`, `GET /v1/status` + `#health-widget`, `GET /v1/quality` + `#quality-widget`). Reste :

| Ticket | Scope | Dépend de | Difficulté |
|---|---|---|---|
| **OBS-005** | Observabilité infra (backend) : uptime checks GCP + log-based metrics + alert policies + OTel → Cloud Logging. | — | moyen |
| **OBS-006** | Route workers `GET /health/dependencies` (état Mistral + vue interne workers) + relais Mistral via les workers + maj contrat OpenAPI (`gateway-to-workers.yml`) + ajout **additif** de la clé `mistral` au body `GET /v1/status` (3 → 4 deps). Spec livrée, impl en cours (`feat/OBS-006-mistral-health`). | OBS-002 | moyen |

Données publiques anonymes restent **sanitisées** : agrégats grossiers, pas de per-user, pas de stack trace ni d'erreur interne brute.

### EPIC OBS-EVAL — Eval prod live (alimente `#quality-widget`)

But : OBS-004 affiche `GET /v1/quality`, mais **rien n'écrit** de run `runner_mode='live'` en prod → le widget renvoie `{"status":"no_data"}`. Cette epic livre le **write path** : un Cloud Run Job exécute le runner Ragas contre les workers prod et persiste **UNE** ligne agrégée `eval_runs`. Spanne code + infra → 3 slices (chacune testable indépendamment).

| Ticket | Scope | Dépend de | Difficulté |
|---|---|---|---|
| **OBS-007** | **Runner OIDC auth** : `eval/clients.py` + `ragas_runner.py` attachent un ID token Google (metadata server) aux appels workers IAM-gated, + dép `google-auth`. Sans ça, l'appel prod → **403** (workers exigent `run.invoker`, pas d'`allUsers`). Code pur, unit-testable (mock token). | — | moyen |
| **OBS-008** | **Image eval-capable** : nouveau `infra/docker/eval.Dockerfile` (package `eval/` + extras `ragas`/`datasets`/`langchain-openai`) + build/push dans `deploy.yml` → Artifact Registry. `workers:latest` ne contient PAS le runner (son Dockerfile copie `workers/` seul + `uv sync --no-dev`). | — | facile |
| **OBS-009** | **Cloud Run Job `archiviste-eval`** (miroir `archiviste-ingest`) : DSN **psycopg2** (PAS `+asyncpg`), `run.invoker` (déjà sur `archiviste_runtime`), secrets Secret Manager (`OPENAI_API_KEY`, `LLM_API_KEY`/Mistral, `RAGAS_JUDGE_PROVIDER=openai`), golden set tiré **au runtime depuis GCS privé**, persist inconditionnel, trigger manuel `gcloud run jobs execute` + runbook. | OBS-007, OBS-008 | difficile |

**Décisions cadrées (conversation 2026-06-09)** :
- Trigger = **manuel** `gcloud run jobs execute archiviste-eval` ; pas de GHA/scheduler (ticket OPS-* aval).
- Golden set **complet** `specs/golden_qa.jsonl` (comparabilité `golden_set_version` avec les runs `eval.yml`) ; pas de cap coût codé (borné par taille set + timeout Job).
- **Persist inconditionnel** (objectif = tuer `no_data`) ; pas de gate seuils, pas de `--baseline`.
- Golden set **reste privé/local** (gitignore : spoilers narratifs, repo public à venir) → livré au Job via **GCS privé au runtime**, jamais via image ni GitHub.
- DSN Job = **psycopg2** : `eval/persist.py` `psycopg2.connect()` rejette le scheme `+asyncpg` du template ingest.
- **Split en 3** : l'epic spanne code (OBS-007) + infra build (OBS-008) + infra runtime (OBS-009) ; bundler casserait la revue et la testabilité indépendante.

Prochaine action : `/spec OBS-007`.

### OPS-001b — Load tests live + rapport SLO

k6 run réel 100 puis 500 VUs contre l'URL prod (scaffold OPS-001a déjà livré). Whitelist IP poste auteur sur Cloudflare + cap Mistral console €30 pendant la fenêtre, archive `summary.json` dans `scripts/load/runs/`, remplit `docs/load-test-report-v1.md` (métriques réelles, verdicts SLO p95<3 s + overhead<80 ms, cold-start, budget réel, lien Langfuse), retire la whitelist. Valide SLOs §SLOs. Trophée portfolio scaling.

### Polish / diffusion

README démo + GIF chat + dashboard · runbook rollback testé sur une revision réelle · raffiner `specs/golden_qa.jsonl` selon corpus final · créer projet Langfuse + clé dans Secret Manager pour traces LLM.

### V2 (post-beta)

- **SEC-002** — rate-limit app-level `tower_governor` + Redis sliding window (si trafic justifie Memorystore).
- **SEC-010** — cost-guard app-level + fallback chain provider (Claude → Mistral → Gemini Flash automatique sur erreur).
- **ING-015** — finalisation GDrive sync automatisé (ING-010/011/012/014 déjà partiellement livré).

Hors scope V1 + V2 : génération images, multi-univers, mobile native.

---

Référence détaillée externe (53 tickets, plan v3) :
`D:\projet-flamme-doudoune\career-ops\reports\projet-lore-rag-tickets.md`.
Nomenclature repo : `FOUND/ING/RET/GEN/EVAL/OBS/SEC/INFRA/DOC/OPS` (cf. `specs/README.md`).
