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

## [0.1.0] - TBD

Initial release.
