# Changelog

All notable changes to this project will be documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Repo scaffolding (CLAUDE.md, .claude/ agents + commands, specs/, docs/, gateway/, workers/, eval/, infra/)
- Workflow Claude Code : architect / implementer / reviewer / eval-runner / debugger sub-agents
- Slash commands : /ticket /plan /impl /review /eval /debug /ship
- ADR 0001 : split Rust gateway + Python workers
- OpenAPI contract gateway-to-workers
- Golden Q/A skeleton + property invariants table
- CI workflows : lint + test + contract + ragas eval
- pre-commit : ruff, fmt, clippy, gitleaks, conventional commits
- **FOUND-001** : minimal viable scaffold — gateway `/healthz` proxying workers `/healthz`, docker-compose dev stack (postgres + gateway + workers), integration test green, CI passing.
- **FOUND-002** : reproducible local stack + boot SLA. Adds `redis` service (auth required, persisted via `redis-data` volume, no host port), `migrator` service under `profiles: ["tools"]` running `migrations/run.sh` (versioned, transactional, gap-detecting), `make migrate` target, `.env.example`, `scripts/measure-boot.sh` writing JSON artefact, dedicated `.github/workflows/boot-sla.yml` (non-blocking), runbook section on migrations + boot baselines.

## [0.1.0] - TBD

Initial release.
