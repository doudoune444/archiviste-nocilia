# Modèle de menaces — Archiviste Nocilia

## Contexte applicatif

- RAG public web. Mix anonyme (lecture RAG) + comptes (features avancées).
- Auth : email + password (argon2id) OU OAuth Google.
- Stockage DB : utilisateurs (email, password_hash, oauth_subject_id, provider), sessions, métadonnées de conversations, tickets lore-gap, embeddings pgvector.
- Stockage GCS : conversations en Markdown.
- Corpus fermé curaté (seul le dev ingère).
- Cadre : projet vitrine entreprise → exigence enterprise-grade.

## Risques prioritaires (priorisés par l'humain)

1. **Abus API → facture LLM explose** (financier).
2. **SEO spam / scraping abusif** (réputation + coût).
3. **Fuite de prompts / secrets** (confidentialité IP).
4. **Malware** (laptop dev / serveur / utilisateurs).

## Périmètre

Dans le périmètre :
- **Gateway** (Rust Axum, public web)
- **Workers** (Python FastAPI, internal-only)
- **DB** (PostgreSQL 16 + pgvector)
- **GCS** (bucket `archiviste-conversations`)
- **Langfuse** (traces LLM, SaaS phase 1)
- **Provider LLM** (Anthropic / OpenAI)

Hors périmètre : laptops dev (risque #4 traité hors threat model app), runners CI.

## Frontières de confiance

À inspecter :
1. Internet → Gateway
2. Gateway → Workers
3. Workers → DB / GCS
4. Workers → LLM
5. Workers → Langfuse

## Matrice STRIDE

### Gateway

| ID | STRIDE | Scénario | Mitigation | Statut |
|---|---|---|---|---|
| G-S-1 | Spoofing | Attaquant prend le compte d'un autre utilisateur | Cookies de session server-side `HttpOnly + Secure + SameSite=Lax`, token 32 octets random hashé argon2id en DB, logout invalide server-side | Retenue |
| G-T-1 | Tampering | MITM modifie le body | HTTPS only, HSTS `max-age=31536000; includeSubDomains; preload` (soumettre la preload list) | Retenue |
| G-R-1 | Repudiation | Utilisateur nie une action | Audit log structuré : `request_id, user_id, route, ip, ua, ts, status` (structlog JSON) | Retenue |
| G-I-1 | Info disclosure | Stack trace fuit dans une erreur | Enveloppe de réponse `{"error": "internal", "request_id": "..."}` + codes HTTP différenciés (401/403/404/422/429/500) | Retenue |
| G-D-1 | DoS (risque #1) | Flood des endpoints → facture LLM | **Défense en profondeur** : Cloudflare edge (absorption DDoS) + `tower_governor` 60 req/min/IP + body cap 1 MiB + circuit breaker LLM (cap €/h, hard stop) | Retenue |
| G-E-1 | Elevation / IDOR | Bob lit `/conversations/{id}` d'Alice | Ownership check dans le handler `WHERE id=$1 AND user_id=$auth` + UUIDv7 (non énumérable) | Retenue |

### Workers

| ID | STRIDE | Scénario | Mitigation | Statut |
|---|---|---|---|---|
| W-S-1 | Spoofing | Attaquant atteint Workers en direct, forge un `user_id` | Cloud Run `ingress=internal` (bind localhost) + HMAC-SHA256 sur header `X-Gateway-Auth` (timing-safe compare) | Retenue |
| W-T-1 | Tampering | Document ingéré contient prompt injection / zero-width | `unicodedata.normalize('NFKC')` + strip control chars + cap de longueur par chunk + allowlist de domaines source (corpus curaté) | Retenue |
| W-I-1 | Info disclosure (risque #3) | Fuite prompt / system prompt visible dans Langfuse | System prompt en role séparé (jamais concaténé avec input utilisateur) + chunks retrouvés dans bloc `<untrusted_data>` XML + `pydantic.SecretStr` redact côté Langfuse + tag `pii=true` skip body log | Retenue |
| W-D-1 | DoS (risque #1) | Floods retrieval = N embeddings + N appels LLM | Timeout 30s par appel LLM + cap `max_tokens` (2k output) + cache embeddings query-hash TTL 1h + budget par utilisateur/jour (compteur Postgres, hard reject si dépassé) | Retenue |
| W-E-1 | Elevation | SSRF via URL de doc → leak token GCP SA | Ingestion = fichiers locaux uniquement, **pas de fetch d'URL**. Si jamais URL nécessaire → ADR + allowlist + reject CIDR privés / metadata | Retenue |

### Database (PostgreSQL + pgvector)

| ID | STRIDE | Scénario | Mitigation | Statut |
|---|---|---|---|---|
| D-S-1 | Spoofing | Fuite DSN → connexion directe | Password DB via Secret Manager + Cloud SQL Auth Proxy (pas d'IP publique) | Retenue |
| D-T-1 | Tampering | SQL injection | Macros `sqlx` (compile-checked) + bind params SQLAlchemy. Zéro concat. + utilisateur DB app-only (pas de DDL/DROP), migrations via SA séparé | Retenue |
| D-I-1 | Info disclosure | Leak de backup / dump disque | Cloud SQL chiffrement par défaut at-rest (AES-256 Google-managed). CMEK différé (pas justifié phase 1) | Retenue |
| D-D-1 | DoS | Slow query épuise le pool | `statement_timeout=10s` + `idle_in_transaction_session_timeout=30s` + pool `max_size=20` | Retenue |
| D-E-1 | Elevation | IDOR via manipulation de FK | Couvert par G-E-1 — toute requête conversation/session DOIT inclure `WHERE user_id = $auth` | Retenue |

### GCS (`archiviste-conversations`)

| ID | STRIDE | Scénario | Mitigation | Statut |
|---|---|---|---|---|
| C-S-1 | Spoofing | Leak SA → lit toutes les conversations | SA Workers `roles/storage.objectAdmin` sur ce bucket uniquement + Uniform bucket-level access + Workload Identity Federation (pas de clé SA JSON statique, token éphémère) | Retenue |
| C-T-1 | Tampering | Bug code → écrase la conversation d'un autre utilisateur | Filename = `{user_id}/{conversation_uuid}.md` (namespace user_id) + Object Versioning ON (rollback 30 jours) + précondition `ifGenerationMatch=0` (refuse l'overwrite) | Retenue |
| C-I-1 | Info disclosure | Bucket public par accident | Uniform bucket-level access ON + Public Access Prevention `enforced` (org policy) | Retenue |
| C-D-1 | DoS | Spam utilisateur crée des conversations à l'infini | Lifecycle rule = delete après 90 jours d'inactivité + quota par utilisateur (compteur DB, cap 1000/user) | Retenue |
| C-E-1 | Elevation | Path traversal via filename | Filename **dérivé serveur uniquement** (UUIDv7 généré côté Workers, jamais d'input utilisateur) + regex `^[0-9a-f-]{36}\.md$` avant write | Retenue |

### Langfuse (observabilité LLM, SaaS)

| ID | STRIDE | Scénario | Mitigation | Statut |
|---|---|---|---|---|
| L-S-1 | Spoofing | Fuite Langfuse API key → traces falsifiées / DROP | `public_key` + `secret_key` via Secret Manager, rotation 90 jours + scope key project-only (pas account-wide) | Retenue |
| L-I-1 | Info disclosure (risque #3) | Fuite PII / secrets dans les traces | Redact côté Workers avant push : regex strip emails, `pydantic.SecretStr` masque les tokens, skip du system prompt complet (tag `prompt_id` uniquement) + tag Langfuse `pii=true` exclu de l'UI | Retenue |
| L-D-1 | DoS | Quota Langfuse explose | Sampling 100% phase 1 (low traffic), drop à 10% si > 10k traces/jour + plan free Langfuse cap = 50k traces/mois, alerte à 80% | Retenue |
| L-E-1 | Elevation | Langfuse SaaS compromis → leak data | Accepté phase 1 (SaaS trust SOC2) + clause « queries logged externally » dans la privacy policy. Migration self-host si claim RGPD sérieux | Risque accepté |

### Provider LLM (Anthropic / OpenAI)

| ID | STRIDE | Scénario | Mitigation | Statut |
|---|---|---|---|---|
| P-S-1 | Spoofing | Fuite API key → facture sur ton compte | API key via Secret Manager + rotation 30 jours + restricted key (allowlist par IP egress Cloud Run) + usage limits org-level + alerting webhook + key séparée prod/dev/CI | Retenue |
| P-T-1 | Tampering | MITM modifie la réponse LLM | HTTPS strict + CA bundle système (default). Cert pinning rejeté (cauchemar ops) | Retenue |
| P-R-1 | Repudiation | Utilisateur nie avoir envoyé une query | Couvert par G-R-1 (audit log gateway) | Retenue |
| P-I-1 | Info disclosure (risque #3) | Provider voit la data | Opt-out training (Anthropic = default no-train, OpenAI = `data_retention=zero` ou Enterprise) + redact PII côté Workers avant le prompt (emails, IDs) | Retenue |
| P-D-1 | DoS (risque #1) | Facture LLM explose | Hard budget cap au niveau provider account (OpenAI usage limits, Anthropic spend alerts) + circuit breaker Workers (W-D-1) lit l'usage API → coupe l'app si seuil dépassé | Retenue |
| P-E-1 | Elevation | Prompt injection escape | Couvert par W-T-1 (corpus poisoning) + W-I-1 (séparation des rôles) | Retenue |

## Transverse

- **Secrets** : GCP Secret Manager uniquement. Pas de `.env` dans les images prod. Types sensibles : `secrecy::Secret<T>` (Rust) / `pydantic.SecretStr` (Python) — OWASP A09.
- **Rotation** : 30j (LLM API keys), 90j (Langfuse, JWT signing si ajouté), à chaque incident (password DB).
- **Stockage password** : `argon2id` (m=19456 KiB, t=2, p=1).
- **OAuth Google** : vérifier `aud` claim = client_id, vérifier `iss` = `accounts.google.com`, vérifier `sub` non vide. Stocker `oauth_subject_id` (immuable), pas `email` comme PK.
- **Login throttling** : 5 tentatives échouées / 15 min / compte → backoff exponentiel.
- **CSP** : `default-src 'self'; object-src 'none'; frame-ancestors 'none'`.
- **CORS** : allowlist explicite des origines, JAMAIS wildcard avec `allow_credentials: true`.
- **Réponse à incident** : `docs/runbook/` (TODO).
- **Pen test** : externe avant le launch public + annuel.

## Cadence de revue

- À chaque ticket `feat/` touchant auth, ingestion ou API externe.
- Revue humaine trimestrielle.
- Après chaque incident.
- Réévaluer le « risque accepté » L-E-1 si le volume PII augmente.

## Questions ouvertes / TODO

- [ ] Confirmer le plan Cloudflare (Free vs Pro pour le bot management).
- [ ] Décider OpenAI Enterprise vs API standard `data_retention=zero` (coût vs garantie).
- [ ] Implémenter le circuit breaker LLM : seuil €/h exact à fixer (proposer €5/h hard cap pour la vitrine).
- [ ] Workload Identity Federation : vérifier le support Cloud Run dans la config Terraform.
- [ ] OAuth Google : choisir la librairie Rust (`oauth2` crate) — ADR ?
- [ ] Privacy policy : rédiger la clause Langfuse + provider LLM (humain).

---

*Workshop conduit le 2026-04-30. STRIDE par composant, 27 scénarios, 26 mitigations retenues + 1 risque accepté.*
