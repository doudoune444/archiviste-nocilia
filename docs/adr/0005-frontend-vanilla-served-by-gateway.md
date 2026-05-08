# ADR 0005 — Frontend MVP : vanilla HTML+JS servi par la gateway

- Status: accepted
- Date: 2026-05-08
- Decider: Doudoune

## Context

Walking skeleton vision §101 est complet côté backend (FOUND-003, ING-001, RET-001, GEN-001, GEN-002, ING-003 mergés). L'API chat round-trip `POST /v1/chat` fonctionne end-to-end mais reste curl-only. Avant `EVAL-001` (Ragas + corpus + golden_qa) il faut une UI fonctionnelle pour :

- Tester manuellement le pipeline 4 modes (mode 1 canon shipped, modes 2/3/4 à venir).
- Démontrer la vitrine portfolio sans imposer un client REST aux visiteurs.
- Boucler le feedback auteur sur la qualité des réponses canon.

Le roadmap historique `archiviste-nocilia-explained-AC/roadmap.md` reportait le choix frontend via DOC-002 (« Streamlit MVP ? aucun ? Next.js ? »). Cet ADR tranche.

## Decision

Le frontend MVP est une **page HTML+CSS+JS vanilla unique servie par la gateway Rust** via `tower-http::services::ServeDir` (ou équivalent `tower-http::ServeFile`).

- Localisation : `gateway/static/` (committé), routé sous `/` + `/assets/*`.
- Aucune build step (pas de Vite, pas de bundler, pas de transpiler). Édition fichier → refresh navigateur.
- `conversation_id` généré côté client (UUIDv4 via `crypto.randomUUID()`), persisté en `localStorage`.
- Aucune dépendance JS tierce phase 1 (pas de React, pas de Vue, pas de htmx). Si une feature requiert une lib (markdown rendering, syntax highlighting), elle sera ajoutée via `<script>` tag CDN versionné, jamais via npm/bundler.
- Auth : `user_tier="anonymous"` continue à être hardcodé côté gateway (vision §73). Pas de cookie session phase 1, persistence purement client-side.

## Rationale

Pourquoi vanilla servi par gateway plutôt qu'un framework et/ou un service séparé :

- **Vertical slice rule (CLAUDE.md `vertical-slice.md`)** — UI MVP fonctionnel ≤ 300 LOC. Un framework (Next.js, Vite+React) impose un scaffold initial qui dépasse ce budget avant la première fonctionnalité.
- **Same-origin = pas de CORS** — la gateway expose déjà `/v1/chat`. Servir `/index.html` depuis la même origine élimine toute configuration CORS et donc toute classe d'erreurs/security-misconfiguration (cohérent `security.md` A05 qui interdit le wildcard CORS).
- **Un seul container Cloud Run** — pas de Terraform module additionnel, pas de domaine secondaire, pas de TLS double.
- **Aucun provider frontend lock-in** — Vercel/Netlify évités. Reste self-host first.
- **Pas de premature abstraction (`clean-code.md`)** — un framework SPA pour 1 page chat = sur-ingénierie. Trois lignes de fetch + DOM manipulation suffisent.
- **Coût cognitif minimal** — debug navigator devtools direct, pas de source-maps, pas de hydration bugs, pas de dépendances Dependabot supplémentaires.
- **Itération rapide auteur** — modifier le ton in-world / le placeholder / la couleur ne demande aucun rebuild ni redeploy frontend séparé.

## Consequences

Positifs :

- Premier ticket UI (`UI-001`) tient en 1 PR vertical slice.
- Zero ajout au pipeline CI (pas de `npm install`, pas de build artifact frontend).
- Surface attaque réduite (pas de chaîne de supply chain npm).
- `cargo deny check` reste seul gate dépendances frontend (parce qu'il n'y en a pas hors `tower-http`).

Négatifs / trade-offs assumés :

- Pas de réactivité framework — un re-render manuel du DOM par message. Acceptable pour 1 chat single-page.
- Si la UI grandit au-delà de ~500 LOC JS, il faudra un nouveau ADR pour évaluer une migration (Vite+React standalone). Critère de migration : >2 pages OU >3 composants réutilisables OU build d'asset (images optimisées, sprites).
- Pas de TypeScript natif → les contrats côté client ne sont pas typés. Mitigation : JSDoc ciblé sur les structures issues de `/v1/chat`.
- Hot-reload nécessite un refresh manuel ou `cargo run` qui sert depuis le filesystem en dev (pas de bundler watcher).

## Triggers de réévaluation

Cet ADR est révoqué et remplacé par un ADR successeur si **une** des conditions suivantes est rencontrée :

1. Plus d'une page distincte est requise (login, dashboard auteur, page admin tickets) — OPS-001 dashboard auteur déclenchera très probablement cette migration.
2. Le code JS dépasse 500 LOC ou 3 fichiers.
3. Markdown rendering / citations cliquables introduisent une dépendance dont la maintenance dépasse l'inclusion CDN simple.
4. SEO ou SSR deviennent une exigence (improbable pour un chat).

## Alternatives considered

- **Streamlit** — rejeté : hors stack `CLAUDE.md` (Python framework UI), exige un service container séparé, esthétique générique nuisant à la vitrine portfolio.
- **Next.js 15 (App Router) standalone** — rejeté phase 1 : scaffold initial dépasse la slice rule, complexity premature pour une page unique. Reconsidéré si OPS-001 multi-page.
- **Vite + React + TS standalone** — rejeté phase 1 : same reason, build step + bundler + CI artifact pour un MVP single-page = sur-ingénierie. Candidat naturel si trigger de réévaluation #1 ou #2 frappe.
- **htmx servi par gateway** — rejeté : ajoute une dépendance JS et un modèle mental (server-rendered partials) sans payoff sur 1 page. Reconsidéré si plusieurs interactions hypermedia émergent.

## References

- `docs/vision.md` §101 walking skeleton (étape UI-001 ajoutée par cet ADR)
- `.claude/rules/vertical-slice.md` — slice ≤ 300 LOC
- `.claude/rules/clean-code.md` — no premature abstraction
- `.claude/rules/security.md` A05 — CORS allowlist
- `gateway/Cargo.toml` — `tower-http` déjà présent
