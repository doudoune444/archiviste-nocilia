# Vision — Archiviste Nocilia

## Pitch (1 phrase)

Chatbot RAG public sur un univers de fiction écrit par l'auteur. Le système classifie l'intent et répond selon 4 modes ; les questions sans réponse créent des tickets « lore-gap » pour guider l'enrichissement du corpus.

## Public cible

- **Lecteurs / fans** de l'univers (anonymes ou inscrits) — posent des questions, lisent les réponses.
- **Auteur** (toi) — consomme les tickets lore-gap pour combler les trous, lit les conversations pour contexte.

## Objectifs

1. **Outil utile** pour l'auteur : feedback loop sur cohérence + trous de l'univers.
2. **Trophée portfolio** : Rust (Axum) + Python (FastAPI) + RAG + GCP + sécurité publique + observabilité + load-tested.
3. **Test scaling crédible** : load tests documentés (100 / 500 users).

## Les 4 modes de réponse

| Mode | Déclencheur | Réponse |
|---|---|---|
| 1 — Canon | `in_domain` + retrieval score ≥ threshold | Réponse cohérente avec lore + citations, ton in-world |
| 2 — Refus poli | `off_topic` | Refus court + suggestion de questions in-domain |
| 3 — Lore-gap | `unknown_in_domain` (in-domain mais retrieval < threshold) | Réponse "noté pour archives" + **création ticket auto** lié à la conversation |
| 4 — Mystère | `acl_blocked` (info `restricted` que ce user n'a pas le droit de voir) | Réponse mystérieuse sans révéler que l'info existe (timing identique aux autres modes) |

## Architecture (rappel synthétique)

Cf. [`architecture.md`](architecture.md) pour le diagramme complet.

- **Gateway Rust (Axum)** : public, perf-critique. Auth JWT + tier, rate-limit Redis sliding window, cost-guard budget LLM, cache, prompt-injection filter, PII scrub, circuit breaker.
- **Workers Python (FastAPI)** : internes, écosystème ML mature. Pipeline LangChain : intent → ACL → retrieve → coherence → answer + conversation_logger + ticket_service.
- **DB** : Postgres + pgvector (chunks, conversations index, tickets, query_log).
- **GCS** : `archiviste-conversations` (1 `.md` append-only par conversation).
- **Observabilité** : Langfuse (traces LLM) + OpenTelemetry (metrics/logs).

## Concepts clés

- **Conversation** : 1 session user = 1 conversation. Persisté en Markdown GCS, indexé Postgres léger. Dashboard auteur ouvre `.md` via signed URL.
- **Ticket lore-gap** : `id`, `conversation_id` (FK), `question`, `category`, `priority_score` (incrémenté si question similaire détectée par cosine ≥ 0.85), `status`. **Aucun autre type de ticket dans l'app.**
- **ACL contenu** : chaque doc lore porte `access_tier` (`public` / `members` / `author_only`). Filtrage post-retrieval.
- **User tiers** : `anonymous` (fingerprint IP+UA+cookie) / `member` (signup) / `author`.

## Source du corpus (3 phases)

L'app **ne fetch jamais d'URL runtime** : le runtime lit uniquement des fichiers locaux. Conforme threat-model W-E-1.

| Phase | Contenu `lore/` | Mécanisme | Ticket |
|---|---|---|---|
| 1 — MVP | `*.md` (frontmatter YAML : `title`, `tags`, `access_tier`) | Édition manuelle locale | ING-001 |
| 2 — Images | + `images/*.png` (schémas, illustrations) | Caption via Vision LLM ou metadata frontmatter | ING-002 |
| 3 — Sync Drive | + export hebdo gdocs→md, gsheets→md tabulaire, gslides→md, images PNG | Script `scripts/gdrive_export.py` (cron hebdo ou trigger manuel), service account `roles/drive.readonly` scope dossier unique | ING-010 / ING-011 / ING-012 (split: core sync gdoc+png / converters gsheet+gslide multi-format / GHA workflow auto-PR) |

Le script de sync est un **outil dev offline**, pas du code applicatif. Pas d'amendement threat-model requis.

## Choix techniques

### Principe : LLM-agnostique

**Aucun provider lock-in.** Le wrapper `workers/src/.../services/llm.py` lit la config via env (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`) et instancie le client LangChain correspondant (`ChatMistralAI` / `ChatAnthropic` / `ChatGoogleGenerativeAI` / `ChatOpenAI` / `ChatDeepSeek`). Tous exposent la même interface `Runnable`. Swap = changement env var + redémarrage container, **zéro changement code applicatif**.

Justification : le provider phase MVP peut tomber, exploser en prix, ou être surclassé. La vitrine doit rester opérable sans rewrite. Fallback chain prévu en SEC-010 (Claude → Mistral → Gemini Flash automatique sur erreur).

### Stack validée

| Composant | Choix | Justification |
|---|---|---|
| LLM generation MVP | `mistral-small-latest` via Mistral API | EU host (RGPD bonus vitrine), excellente qualité FR, ~0,10 € / ~0,30 € par M tok. Cheap pour itérer. |
| LLM intent classifier MVP | `mistral-small-latest` (single provider) | Simplifie phase 1. Split en Haiku/Mistral si besoin perf coût plus tard. |
| Provider abstraction | LangChain `ChatModel` interface + wrapper config-driven | Swap provider sans changement code. |
| Embeddings | `mistral-embed` via Mistral API (dim 1024) | Multilingue FR/EN, EU host (RGPD), même provider que LLM (1 secret partagé), pas de modèle 568M dans container Cloud Run scale-to-zero. Dim 1024 identique BGE-M3 → zéro migration DB. Fallback `BAAI/bge-m3` self-host documenté (même dim) si vendor concentration problématique V2. |
| Vector DB | Postgres + pgvector | Déjà en infra (FOUND-002). |
| Auth phase MVP | Aucune. `user_tier="anonymous"` hardcodé gateway → workers | Pipeline RAG testable sans complexité auth. SEC-001 plus tard. |
| Frontend MVP | Page HTML+CSS+JS vanilla servie par la gateway (`gateway/static/`) via `tower-http::ServeDir`. `conversation_id` UUIDv4 généré côté client + `localStorage`. Aucune build step, aucune dépendance JS tierce phase 1. | Vertical slice ≤ 300 LOC, same-origin = zéro CORS, 1 container Cloud Run, pas de supply chain npm. Cf. [`adr/0005-frontend-vanilla-served-by-gateway.md`](adr/0005-frontend-vanilla-served-by-gateway.md). |

**Note compte Claude Max** : utilisable pour dev (claude.ai, Claude Code CLI) mais **pas programmatiquement**. App workers Python utilise une API key séparée du provider choisi (Mistral pour MVP).

## Non-goals (phase 1)

- Pas de multi-univers / multi-tenant (1 univers, 1 auteur).
- Pas de mémoire conversationnelle multi-turn complexe.
- Pas de génération illustrations (img2img).
- Pas de fine-tuning LoRA dédié.
- Pas de mobile app native.
- Pas de gRPC interne (HTTP/JSON suffit phase 1).

## SLOs cibles

- Disponibilité : 99,0 % mensuel.
- Latence : p95 chat round-trip < 3 s.
- Eval Ragas : faithfulness ≥ 0,85, answer_relevancy ≥ 0,85 sur golden set.
- Overhead Rust gateway : < 80 ms p95 à 500 users concurrents.

## Plan global (6 EPICs)

1. **FOUND** — scaffold + DB + ingestion + RAG basique + premier déploiement (FOUND-001/002 shipped).
2. **ING / RET / GEN** — pipeline LangChain 4 modes + persistence conversations.
3. **UI** — chat page MVP servie par gateway (vanilla HTML+JS), puis dashboard auteur (OPS-*).
4. **SEC** — auth + rate-limit + cost guard + cache + injection defense + PII.
5. **OBS** — Langfuse + structured logs + Ragas eval + load tests.
6. **OPS / DOC** — CI/CD complet + Terraform + README/ADR + démo + diffusion.

## Walking skeleton MVP — ordre d'attaque

### Déjà livré (résumé)

Backend chat round-trip end-to-end + UI chat publique + Ragas eval CI. Pipeline : ingestion `lore/*.md` (+ GDrive sync export) → retrieve top-K pgvector → generate mode 1 canon (Mistral Small) → gateway `/v1/chat` → workers `/v1/generate` → persistence conversation Markdown GCS + index Postgres. Chat HTML servi par gateway (`static/`). Tickets mergés : FOUND-003, ING-001/003/010/011/012/013/014, RET-001, GEN-001/002, UI-001, EVAL-001, INFRA-001.

État : application curl-able + page chat fonctionnelle en local. Pas encore déployée publiquement.

### Cible deploy V1 beta

Stack vitrine grande entreprise — full GCP managed + IaC. Région **europe-west9** (Paris, RGPD résidence FR).

- **Cloud Run** gateway 256 MB + workers 512 MB, scale-to-zero. Domain mapping europe-west9.
- **Cloud SQL** Postgres 16 + extension `vector`, `db-f1-micro` + 10 GB. Auth Proxy sidecar Unix socket (pas de VPC connector).
- **GCS** bucket `archiviste-conversations`, uniform bucket-level access, lifecycle TTL 30j.
- **Secret Manager** — 1 secret partagé `MISTRAL_API_KEY` (LLM + embed même provider).
- **Cloudflare** front (free tier) — DNS, TLS Full Strict, Bot Fight Mode, 1 rule rate-limit 100 req/min/IP. Remplace Redis sliding window pour V1.
- **Domaine** `archiviste.nocilia.fr` (`.fr` primary + 301 redirects depuis `.com` / `.org` / `.eu` / `.net` via Cloudflare Page Rules).
- **Terraform** — IaC complet (ADR-0003 réactivé : `infra/terraform/` source de vérité).
- **GHA deploy** — Workload Identity Federation (zéro JSON key), build → push Artifact Registry → deploy canary 0 % traffic → smoke test → promote 100 % ou auto-rollback `gcloud run services update-traffic --to-revisions=PREVIOUS=100`.
- **IAM** — 1 SA deploy `gha-deploy@` (`run.admin`, `artifactregistry.writer`, `cloudsql.client`, `secretmanager.secretAccessor`, `iam.serviceAccountUser`) + 1 SA runtime partagé `archiviste-runtime@` (`cloudsql.client`, `secretmanager.secretAccessor`, `storage.objectAdmin` bucket-scoped). OIDC trust strict : `attribute.repository == 'doudoune444/archiviste-nocilia' && attribute.ref == 'refs/heads/main'`.
- **Cloudflare tuning** — Bot Fight Mode ON, Security Level Medium, Challenge Passage 30 min, pas de Turnstile V1.
- **Rollback** — runbook `docs/runbook/rollback.md` (3 commandes gcloud). DB safety net = Cloud SQL PITR backup automatique 7j.

**Pas en V1** : Redis / Memorystore, VPC connector, app-level cost-guard, sliding window rate-limit, observabilité full (uptime checks + log-based metrics + alert policies multiples).

Budget estimé V1 : **~15-18 €/mois** (Cloud SQL ~11 €, Cloud Run gateway ~1 €, workers ~2-3 €, Mistral API ~1-2 €, misc <1 €, Cloudflare 0 €). Alert budget GCP €50/mois → email.

### Décisions V1 beta (Q1-Q16)

| # | Sujet | Décision | Raison |
|---|---|---|---|
| Q1 | Stack | Cloud Run + Cloud SQL + Cloudflare | Vitrine max grande entreprise, full managed, free tier généreux. |
| Q2 | Région GCP | europe-west9 Paris | RGPD résidence FR, latence audience FR. |
| Q3 | Headers sécurité | SEC-003 ticket séparé | HSTS / CSP / nosniff / Referrer-Policy hors INFRA-002 pour PR ≤ 300 LOC. |
| Q4 | Observabilité V1 | Budget alert seul + Langfuse free | Skip uptime checks / log-metrics / alerts multiples → OBS-001 V2. |
| Q5 | Domaine | `archiviste.nocilia.fr` | Subdomain branding univers, `.fr` primary + 301 redirects autres TLDs. |
| Q6 | Cloud Run sizing | Gateway 256 MB / workers 512 MB scale-to-zero | Cold start acceptable (~3 s gateway, ~5 s workers sans modèle bundlé). |
| Q7 | Embeddings | Mistral `mistral-embed` (dim 1024) | EU host, même provider que LLM, dim 1024 identique BGE-M3 = zéro migration DB. Fallback BGE-M3 self-host documenté. |
| Q8 | Cost-guard | Cap Mistral console (V1) | Suffit pour beta, app-level fallback chain = SEC-010 V2. |
| Q9 | Rate-limit | Cloudflare rule 100 req/min/IP | Free tier, remplace Redis sliding window. SEC-002 = V2. |
| Q10 | Redis | Aucun V1 | Memorystore ~30 €/mois + VPC connector ~10 €/mois = 40 €/mois injustifié beta. |
| Q11 | Cloud SQL access | Auth Proxy sidecar Unix socket | Pas de VPC connector requis, IAM auth. |
| Q12 | GCS lifecycle | TTL 30 j sur conversations | Beta = rétention minimale, ajustable post-feedback. |
| Q13 | IaC | Terraform activé | ADR-0003 réactivé, `infra/terraform/` source de vérité. |
| Q14 | GHA auth | Workload Identity Federation | Zéro JSON key SA, OIDC trust repo + branch `main`. |
| Q15 | Deploy strategy | Canary 0 % traffic + smoke test + auto-rollback | Cloud Run traffic split natif, zéro coût. |
| Q16 | Eval gate CI deploy | A min : smoke test post-deploy auto + Ragas live manuel | Eval CI offline reste gate PR, live = artefact vitrine on-demand. |
| Q17 | WIF / IAM scope | 1 SA deploy + 1 SA runtime partagé, OIDC trust repo + `main` | V1 fast. Split runtime SA = V2 quand SEC-001 atterrit. |
| Q18 | Rollback | Runbook 3 cmds `gcloud run services update-traffic` + PITR Cloud SQL | Beta = pas de SLA, détection manuelle suffit, PITR backup gratuit `db-f1-micro` 7j. |
| Q19 | Cloudflare bot/security | Bot Fight Mode ON, Security Level Medium, Challenge 30 min | Free tier, zéro friction utilisateurs légitimes. Turnstile = V2 si trafic bot anormal. |
| Q20 | Ordre attaque final | 9 tickets V1 (ship-able #2 INFRA-002) + V2 list (SEC-002 / SEC-010 / OBS-001 / ING-015) | Vertical slice ≤ 300 LOC chacun, V1 fast / V2 ~1 semaine après. |

### Ordre d'attaque à partir de maintenant

Beta ship-able après **#2**. Ordre conçu pour minimiser risque public (Cloudflare rate-limit + cap Mistral console AVANT exposition, hardening app-level V2).

1. **Corpus + golden_qa réels** — tâche auteur (hors-ticket code) : `lore/*.md` contenu canon + `specs/golden_qa.jsonl` ≥ 30 entrées 4 modes. ✅ déjà 46 entrées, à raffiner selon corpus final.
2. **INFRA-002 deploy GCP beta** — Terraform Cloud Run + Cloud SQL + GCS + Secret Manager + Cloudflare. Embedder switch BGE-M3 → `mistral-embed`. GHA `deploy.yml` WIF + canary + smoke + auto-rollback. **Premier ship public** sur `https://archiviste.nocilia.fr`. ✅ code mergé (a/b/c/d), `terraform apply` réel + 1er deploy = étape D ci-dessous.
3. **SEC-003 security headers** — HSTS / CSP / X-Content-Type-Options / Referrer-Policy via tower middleware gateway. Quick win post-deploy. ✅
4. **GEN-003 Mode 2 off-topic** — refus poli quand intent classifier renvoie `off_topic`. Polish UX immédiatement visible chat. ✅
5. **GEN-004 Mode 3 lore-gap + ticket** — retrieval < threshold → "noté pour archives" + création ticket auto (table `tickets`, dedup cosine ≥ 0.85). Différenciateur core produit. ✅ (a+b)
6. **UI-002 dashboard auteur tickets** — page liste tickets lore-gap + liens conversations GCS signed URL. Ferme boucle feedback auteur. ⚠️ PR1 backend ✅, PR2 frontend (`UI-002b`) reste à livrer.
7. **SEC-001 auth tiers** — JWT + `anonymous` (fingerprint) / `member` (signup) / `author`. Pré-requis Mode 4 ACL + dashboard auteur sécurisé. ⚠️ PR-a infra ✅ (JWT verify + `/v1/me` + fingerprint + sessions check). PR-b runtime auth (`POST /v1/auth/{signup,login,logout}` + throttle + argon2id) reste à livrer.
8. **GEN-005 Mode 4 mystère + ACL** — filtrage post-retrieval sur `access_tier`, réponse mystérieuse (timing constant) si `acl_blocked`. Feature 4 modes complète. ✅
9. **OPS-001 load tests 100/500 users** — k6 scripts + rapport SLO (p95 < 3 s, gateway overhead < 80 ms). Trophy portfolio scaling. ⚠️ OPS-001a scaffold ✅ ; OPS-001b run live + rapport rempli reste à livrer.

### Ordre suggéré V1 ship public (état 2026-05-25)

État courant : tickets 1→9 mergés sauf compléments UI-002b, SEC-001 PR-b, OPS-001b. Aucun `terraform apply` réel exécuté — DNS `archiviste.nocilia.fr` ne résout pas encore. 5 PRs Dependabot ouvertes (dont `jsonwebtoken 9→10` auth-critique).

Ship-able public après **D**. Trophée portfolio complet après **E**.

| Étape | Travail | Pourquoi maintenant |
|---|---|---|
| **A** | Review + merge 5 PRs Dependabot (`jsonwebtoken 10`, `axum-extra`, `rand_core`, `config`, patch-group) | `jsonwebtoken 10` touche le chemin auth SEC-001 — fixer la base avant PR-b. Réduit risque CVE avant exposition publique. |
| **B** | **SEC-001 PR-b** — `POST /v1/auth/signup` / `login` / `logout`, argon2id (m=19456,t=2,p=1), throttle login (5 fails / 15 min / email), `Set-Cookie archiviste_session`. AC-1..8, AC-17, AC-18, AC-19 SEC-001. | Sans login auteur impossible → dashboard inutile prod, Mode 4 ACL non démontrable. Bloque B, C, F. |
| **C** | **UI-002b** — `gateway/static/dashboard.html` + `assets/dashboard.{js,css}` + handler `/dashboard` gated `author_only` (plan ~216 LOC déjà écrit). | Sans page HTML le backend `/v1/tickets` reste invisible. Ferme boucle feedback auteur. |
| **D** | **Deploy live GCP** — `terraform apply` (Cloud Run + SQL + GCS + Secret Manager + Cloudflare), 1er run `deploy.yml` push main, exécution `scripts/seed_author.sql` en prod, smoke `GET https://archiviste.nocilia.fr/healthz` = 200 + canary promotion 100 %. | **Premier ship public**. Active toutes les fonctionnalités déjà mergées. Pré-requis E. |
| **E** | **OPS-001b** — whitelist IP poste auteur sur Cloudflare, cap Mistral console €30, run k6 100 puis 500 VUs contre URL prod, archive `summary.json` dans `scripts/load/runs/`, remplit `docs/load-test-report-v1.md` (métriques, verdicts SLO p95<3 s + overhead<80 ms, cold-start, budget réel, lien Langfuse), retire whitelist. | Bloqué par D (besoin URL prod). Valide SLO vision §87-92. Trophée portfolio scaling. |
| **F** | **Polish démo** — README démo + GIF chat + dashboard, runbook rollback testé sur une revision réelle, raffiner `specs/golden_qa.jsonl` selon corpus final, créer projet Langfuse + clé dans Secret Manager pour traces LLM. | Diffusion vitrine. Non bloquant pour ship mais nécessaire avant publication portfolio. |

V1 ship-able après **D**. Trophée portfolio complet après **E**.

**V2 (post-beta, ~1 semaine après)** :
- **SEC-002** rate-limit app-level `tower_governor` + Redis sliding window (si trafic justifie Memorystore).
- **SEC-010** cost-guard app-level + fallback chain Claude → Mistral → Gemini Flash.
- **OBS-001** full observability — uptime checks + log-based metrics + alert policies + OTel → Cloud Logging.
- **ING-015** finalisation GDrive sync automatisé (ING-010/011/012/014 déjà partiellement livré).

Pas en scope V1 + V2 : génération images, multi-univers, mobile native.

Référence détaillée externe (53 tickets, plan v3) :
`D:\projet-flamme-doudoune\career-ops\reports\projet-lore-rag-tickets.md`.
Note : la nomenclature de ce repo (`FOUND/ING/RET/GEN/EVAL/OBS/SEC/INFRA/DOC/OPS` cf. `specs/README.md`) diffère de la doc plan (`FOUND/CHAIN/SEC/OBS/SHIP`). Mapping au cas par cas dans les specs.
