# ADR 0009 — JWT EdDSA + server-side sessions + argon2id

- Status: proposed
- Date: 2026-05-18
- Decider: Doudoune

## Context

SEC-001 introduit l'authentification utilisateur (tiers `anonymous` / `member` / `author`). Trois choix critiques bloquent `/plan` et figent l'API surface :

1. **Algorithme JWT** — `security.md` §A02 exige pin `alg` `RS256` ou `EdDSA`, rejet de `none` et alg hors allowlist.
2. **Modèle session** — JWT stateless (logout impossible côté serveur) vs sessions server-side hashées révocables. `security.md` §A07 impose « Logout invalidates server-side token (no JWT-only logout) ».
3. **Paramètres argon2id** — `security.md` §A02 fige `m=19456 KiB, t=2, p=1` ; ADR consolide la décision pour références futures (reset password V2, MFA author).

L'app cible Cloud Run scale-to-zero, single replica typique V1 (vision Q1+Q6). Les vérifs auth doivent rester < 5 ms p95 sur hot path (cf. SEC-001 Performance/SLO). Le `jsonwebtoken` crate Rust 9.x est dep nouvelle « lourde » au sens `CLAUDE.md` § Règles humaines → ADR requis avant merge.

## Decision

### 1. JWT alg = EdDSA (Ed25519)

- Header obligatoire : `{"alg":"EdDSA","typ":"JWT","kid":"<key-id>"}`.
- Vérification rejette toute valeur `alg` ≠ `EdDSA` (allowlist stricte, pas de fallback).
- Clé Ed25519 générée hors-ligne par l'humain (`openssl genpkey -algorithm ed25519 -out jwt_ed25519_private.pem`), partie privée dans Secret Manager `JWT_ED25519_PRIVATE_KEY_PEM`, partie publique dans `JWT_ED25519_PUBLIC_KEY_PEM`. Aucune clé hardcodée ni fallback dev.
- Gateway charge la clé au boot via env var typée `secrecy::SecretString` ; échec de chargement = refus de démarrage (`fatal: jwt_private_key_load_failed`).

### 2. Sessions server-side hashées révocables

- Table `sessions(id UUID PK, user_id UUID FK, token_hash TEXT, created_at, expires_at, revoked_at NULL)`.
- JWT payload embarque `sid` (session UUID) ; chaque requête authentifiée frappe `sessions` pour vérifier `revoked_at IS NULL AND expires_at > NOW()`.
- Logout = `UPDATE sessions SET revoked_at = NOW()` ; cookie effacé côté client. Aucun « logout JWT-only ».
- `token_hash` = argon2id du token brut (paramètres ci-dessous), jamais le token brut en DB.
- Pas de cache phase 1 (single replica) ; latence Postgres locale < 2 ms typique acceptable cible < 5 ms p95.

### 3. Argon2id paramètres

- `m=19456 KiB` (19 MiB), `t=2` (iterations), `p=1` (parallelism).
- Sel 16 octets `rand::rngs::OsRng` par hash.
- Hash de référence constant (pré-calculé au boot) pour timing-safe sur cas `email_taken` / `invalid_credentials` (cf. SEC-001 AC-2/AC-6).
- `m=19456 KiB` cible : ~150-200 ms p95 sur Cloud Run `db-f1-micro` ; aligné OWASP 2024 cheatsheet (≥ 19 MiB).

### 4. Crates (Cargo gateway)

| Concern | Crate | Version min | Note |
|---|---|---|---|
| JWT EdDSA | `jsonwebtoken` | `9.0` | Feature `use_pem` ; supporte EdDSA via `Algorithm::EdDSA`. |
| Ed25519 | `ed25519-dalek` | `2.0` | Transitif via `jsonwebtoken` mais peut être pin direct. |
| Password hash | `argon2` | `0.5` | Pure Rust, audit-reviewed. |
| Secret type | `secrecy` | `0.10` | Déjà workspace dep (ADR-0002). |

### 5. Politique `kid` rotation

- Phase 1 (V1 beta) : **un seul `kid` actif**, rotation manuelle via redéploiement (génération nouvelle clé + remplacement secret + restart Cloud Run). Tous les JWTs signés avec ce `kid`.
- Phase 2 (V2 + SEC-* dédié) : support multi-`kid` via JWKS file ou env var multi-PEM, ancien `kid` accepté en vérif pendant fenêtre overlap (durée = JWT TTL = 7j) avant retrait.
- `kid` value : timestamp génération (`YYYYMMDD`) ou UUIDv4 court ; documenté dans `docs/runbook/key-rotation.md` (créé en V2).

## Consequences

### Positive

- EdDSA signatures = 64 octets (vs RS256 256 octets) → JWT plus court, parse plus rapide.
- EdDSA verify ~3-5× plus rapide que RS256 sur Cloud Run.
- Server-side sessions = logout effectif + révocation incident (clé compromise → `UPDATE sessions SET revoked_at = NOW()` global).
- Argon2id paramètres figés = comparable cross-déploiements, alerte régression perf si dépassement budget 200 ms.
- Pin `alg=EdDSA` strict élimine classe vulnérabilités alg confusion (CVE-2015-9235, etc.).

### Negative

- `jsonwebtoken` 9.x dep nouvelle ~50 KLOC transitif (ed25519-dalek, sha2, base64, serde_json — la plupart déjà workspace). Audit `cargo deny check` requis avant merge.
- Vérif session par requête = 1 round-trip Postgres par hit auth → contention DB si single replica + burst. Mitigation cache V2 (Redis SEC-002).
- Rotation `kid` manuelle V1 = window vulnérabilité si clé fuit (jusqu'à redéploiement). Acceptable beta, multi-`kid` V2.
- Argon2 `m=19456 KiB` × 1 hash par login = pic mémoire 19 MiB ponctuel ; sur Cloud Run gateway 256 MiB (vision Q6), reste sous 10 % RAM.

### Neutral

- Possibilité de migrer vers RS256 sans changement contrat externe (header `alg` change, payload identique). Coût migration = nouvelle clé + nouveau code path verify.
- Format `kid` libre, pas de contrainte standard.

## Alternatives considered

- **RS256** — rejected : signatures 4× plus longues, verify 3-5× plus lent, taille clé 2048 bits ≫ Ed25519 256 bits sans bénéfice sécu équivalent (Ed25519 ≥ 128 bits sécurité standard).
- **HS256 (HMAC partagé)** — rejected : secret unique partagé gateway + futurs services (workers) = mauvais multi-service hygiene, rotation casse tous les consommateurs simultanément. EdDSA = clé publique distribuable séparément.
- **JWT stateless (pas de table `sessions`)** — rejected : viole `security.md` §A07 « no JWT-only logout », pas de révocation incident, pas de « logout all devices » futur. Le coût 1 round-trip DB est budget acceptable.
- **bcrypt** — rejected : ADR-0002 + OWASP 2024 marque comme « no longer recommended for new projects ». Argon2id seul standard moderne.
- **Argon2id `m=65536 KiB` (OWASP « strong »)** — rejected : 65 MiB × N logins concurrent = risque OOM Cloud Run 256 MiB. `m=19456 KiB` = palier OWASP « default » suffisant beta, hardening V2 si profil menace évolue.
- **`jose` crate (alternative JWT)** — rejected : moins mature, surface API plus large, `jsonwebtoken` 9.x suffit pour notre besoin et est déjà mentionné ADR-0002.

## References

- `.claude/rules/security.md` §A02 (Cryptographic Failures), §A07 (Authentication Failures), §A09 (Logging — `secrecy::SecretString`).
- `docs/adr/0002-security-baseline-crates.md` — baseline crates (jsonwebtoken, argon2, secrecy).
- `specs/acceptance/SEC-001.md` AC-1, AC-4, AC-5, AC-12, AC-13 (paramètres argon2id, JWT alg pin, vérif session).
- `specs/plans/SEC-001.md` D-2 (`jsonwebtoken` 9.x + `use_pem`).
- OWASP Cheat Sheet : Password Storage 2024, JSON Web Token Cheat Sheet.
- RFC 8037 (CFRG EdDSA in JOSE).
