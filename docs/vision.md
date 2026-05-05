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
| 3 — Sync Drive | + export hebdo gdocs→md, gsheets→md tabulaire, images PNG | Script `scripts/gdrive-export.py` (cron hebdo ou trigger manuel), service account `roles/drive.readonly` scope dossier unique | ING-010 |

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
| Embeddings | `BAAI/bge-m3` self-host via `sentence-transformers` | Multilingue FR/EN, 568M params, CPU acceptable phase MVP. Fallback `paraphrase-multilingual-MiniLM-L12-v2` si latence trop haute. |
| Vector DB | Postgres + pgvector | Déjà en infra (FOUND-002). |
| Auth phase MVP | Aucune. `user_tier="anonymous"` hardcodé gateway → workers | Pipeline RAG testable sans complexité auth. SEC-001 plus tard. |

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

## Plan global (5 EPICs)

1. **FOUND** — scaffold + DB + ingestion + RAG basique + premier déploiement (FOUND-001/002 shipped).
2. **ING / RET / GEN** — pipeline LangChain 4 modes + dashboard auteur.
3. **SEC** — auth + rate-limit + cost guard + cache + injection defense + PII.
4. **OBS** — Langfuse + structured logs + Ragas eval + load tests.
5. **OPS / DOC** — CI/CD complet + Terraform + README/ADR + démo + diffusion.

## Walking skeleton MVP — ordre d'attaque

1. **FOUND-003** — Schéma DB (`documents`, `chunks` pgvector, `conversations`, `tickets`, `query_log`, `users`).
2. **ING-001** — Ingestion `lore/*.md` local (parse frontmatter + chunk + embed bge-m3 + upsert idempotent).
3. **RET-001** — Endpoint `/v1/retrieve` top-K cosine (sans rerank phase 1).
4. **GEN-001** — Endpoint `/v1/generate` mode 1 canon (LLM via wrapper config-driven, MVP = Mistral Small + citations + ton in-world).
5. **GEN-002** — Forwarder gateway `/v1/chat` → workers (`request_id`, tier hardcodé `anonymous`).
6. **ING-003** — Conversation logger (Markdown GCS + index Postgres).
7. **EVAL-001** — Ragas runner sur golden_qa (auteur écrit le set après GEN-001 fonctionne).

Modes 2/3/4 (off-topic, lore-gap, mystère), ACL, GDrive sync, auth, cache → après le skeleton.

Référence détaillée externe (53 tickets, plan v3) :
`D:\projet-flamme-doudoune\career-ops\reports\projet-lore-rag-tickets.md`.
Note : la nomenclature de ce repo (`FOUND/ING/RET/GEN/EVAL/OBS/SEC/INFRA/DOC/OPS` cf. `specs/README.md`) diffère de la doc plan (`FOUND/CHAIN/SEC/OBS/SHIP`). Mapping au cas par cas dans les specs.
