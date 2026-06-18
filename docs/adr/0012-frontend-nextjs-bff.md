# ADR 0012 — Frontend Next.js BFF (supersedes ADR-0005)

- Status: accepted
- Date: 2026-06-18
- Decider: Doudoune

## Context

ADR-0005 a tranché un frontend MVP vanilla HTML+CSS+JS servi par la gateway Rust, sans build step ni dépendance npm. Ce choix était correct pour un chat single-page curl-only. Il prévoyait explicitement ses propres triggers de révocation (§Triggers de réévaluation) :

1. Plus d'une page distincte requise (login, dashboard auteur, board, observabilité).
2. JS > 500 LOC ou 3 fichiers.
3. Markdown rendering / citations introduisant une dépendance non-CDN.
4. SSR/SEO requis.

Les six PRDs WEB-* (#185–#190) déclenchent les triggers 1, 2 et 3 simultanément : cinq vues distinctes (chat, auth, board, dashboard, observabilité), rendering Markdown sanitizé, navigation partagée auth-aware, et un besoin de Server Components (RSC) pour appeler la gateway server-side sans exposer le backend au navigateur. Le vanilla servi par la gateway ne peut plus porter ce produit sans devenir ingérable et non typé.

Le propriétaire veut par ailleurs démontrer la stack cible : TypeScript / Node.js / Next.js App Router.

## Decision

Le frontend devient une **application Next.js (App Router, React Server Components, TypeScript) dans `frontend/`**, déployée comme **second service Cloud Run** placé devant la gateway Rust en **Backend-for-Frontend (BFF)**.

- **Topologie.** Navigateur → Next.js (RSC + route handlers) → gateway Rust (API only). La gateway n'est jamais joignable directement par le navigateur. Pas de CORS : le navigateur ne voit qu'une seule origine.
- **Auth reste en Rust.** Aucune logique de session réimplémentée en Node. Le BFF relaie `/v1/auth/*` vers la gateway, repasse les `Set-Cookie` au navigateur sur l'origine Next.js, et forwarde `archiviste_session` + `archiviste_anon` à chaque appel backend. Logout, throttling, validation JWT/session restent entièrement dans la gateway.
- **Module profond `bff-proxy`.** Seul endroit serveur qui appelle la gateway pour le compte de la requête courante : injecte les cookies forwardés, propage le request id, relaie les `Set-Cookie`. Interface `forward(request, gatewayPath) -> response`. Seul module connaissant l'URL gateway et les noms de cookies.
- **Rendering.** Server Components par défaut ; Client Components seulement où l'interactivité l'exige (streaming chat, formulaires auth, polling health).
- **Styling.** CSS Modules, pas de component library. Tailwind explicitement non adopté. Tokens de design existants réutilisés (near-black, warm-brown, off-white).
- **Langue.** Français uniquement, pas de framework i18n.
- **CSP.** Le frontend émet une CSP au moins aussi stricte que celle de la gateway (`default-src 'self'; object-src 'none'; frame-ancestors 'none'`), sans inline non nonce/hash-bound.
- **Déploiement.** Second service Cloud Run, `min-instances=0` (scale-to-zero, cold starts acceptés), géré par le Terraform existant. La gateway devient API-focused ; l'origine web publique est le service Next.js.
- **CI.** Gate frontend : typecheck + lint + Vitest (unit) + Playwright (smoke).

## Consequences

Positifs :

- Type safety end-to-end côté client ; stack alignée sur la cible portfolio.
- Toutes les vues WEB-* (chat, auth, board, dashboard, obs) débloquées sur une fondation commune.
- Same-origin préservé du point de vue navigateur → cookies httpOnly et CSP stricte continuent de fonctionner, **aucun CORS introduit**.
- Surface d'attaque backend réduite : gateway non joignable directement.

Négatifs / trade-offs assumés :

- Build step réintroduit (Next.js build) → ajout au pipeline CI (`npm`/`pnpm install`, build artifact, deps Dependabot/audit npm).
- Second service Cloud Run → un module Terraform additionnel et un déploiement de plus (coût ~nul à `min-instances=0`, cold starts acceptés en trafic portfolio).
- Chaîne d'approvisionnement npm réintroduite → `pip-audit`/`cargo deny` ne couvrent plus le frontend ; un audit npm (`npm audit` / `osv-scanner`) entre dans les gates.
- Complexité RSC/hydration vs vanilla (source-maps, hydration bugs possibles) — acceptée pour le gain produit.

## Alternatives considered

- **Garder le vanilla d'ADR-0005** — rejeté : déclenche 3 des 4 triggers de révocation que l'ADR-0005 fixait lui-même ; non typé, non scalable sur 5 vues, pas de RSC pour le pattern BFF.
- **Vite + React SPA standalone** — rejeté : SPA client-side exposerait la gateway au navigateur (CORS requis) ou nécessiterait un proxy maison ; pas de Server Components ni de pattern BFF natif ; hors stack cible affichée.
- **Next.js déployé sur Vercel** — rejeté : provider lock-in et sortie du modèle self-host-first ; Cloud Run garde tout sur GCP, cohérent avec gateway/workers.
- **htmx + partials servis par la gateway** — rejeté : pas de type safety, pas la stack cible, modèle hypermedia inadapté au streaming chat token-par-token.

## References

- PRD #185 — WEB-PLATFORM (Next.js BFF migration)
- Issue #191 — PLATFORM-001 (ce ticket ; note : le corps mentionne « ADR-0006 », numéro déjà pris par `0006-gdrive-api-client.md` ; numéro libre suivant = `0012`)
- `docs/adr/0005-frontend-vanilla-served-by-gateway.md` — superseded par cet ADR
- `.claude/rules/security.md` A05 (CORS allowlist), A10, RAG output sanitization
- PRDs aval bloqués : #186 WEB-CHAT, #187 WEB-AUTH, #188 WEB-BOARD, #189 WEB-DASH, #190 WEB-OBS
