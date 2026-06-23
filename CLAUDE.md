# Archiviste Nocilia

RAG public web multi-utilisateurs. Gateway Rust (Axum) + workers Python
(FastAPI/LangChain). Persistence conversations Markdown sur GCS.

## Agent skills

### Issue tracker

Issues in GitHub Issues (`doudoune444/archiviste-nocilia`), via the `gh` CLI.
External PRs are not a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

No glossary or ADRs set up yet. If `CONTEXT.md` or `docs/adr/` appear later,
read them before exploring. See `docs/agents/domain.md`.
