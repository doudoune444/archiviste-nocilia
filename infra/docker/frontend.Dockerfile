# syntax=docker/dockerfile:1.7
# PLATFORM-004: multi-stage Next.js standalone build.
# Context = frontend/ (set via `context: frontend` in deploy.yml).
# Cloud Run sets $PORT=8080; next start honors it — no ports{} override needed.

# WHY digest pin: reproducible builds — a floating tag silently picks up a new
# base layer on every rebuild.  Digest below is node:22-alpine linux/amd64
# (sha256 resolved 2026-06-18 via `docker manifest inspect node:22-alpine`).
# Update by re-running: docker manifest inspect node:22-alpine | jq -r
#   '.manifests[] | select(.platform.architecture=="amd64" and .platform.os=="linux") | .digest'
ARG NODE_DIGEST=sha256:5e8888a165087a80513a7e773bb1a60c2e7dd54ac7cddab404ae2f470815e8e8

# ── Stage 1: deps ────────────────────────────────────────────────────────────
FROM node:22-alpine@${NODE_DIGEST} AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
# npm ci is already frozen — --frozen-lockfile is a yarn/pnpm flag, ignored by npm.
RUN npm ci

# ── Stage 2: builder ─────────────────────────────────────────────────────────
FROM node:22-alpine@${NODE_DIGEST} AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_TELEMETRY_DISABLED prevents outbound telemetry calls during build.
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ── Stage 3: runtime ─────────────────────────────────────────────────────────
FROM node:22-alpine@${NODE_DIGEST} AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# SEC: run as a non-root user — defence-in-depth against container escape.
RUN addgroup -S app && adduser -S app -G app
COPY --from=builder --chown=app:app /app/.next/standalone ./
COPY --from=builder --chown=app:app /app/.next/static ./.next/static
COPY --from=builder --chown=app:app /app/public ./public

USER app

# Cloud Run always injects PORT=8080; next start reads it automatically.
EXPOSE 8080
CMD ["node", "server.js"]
