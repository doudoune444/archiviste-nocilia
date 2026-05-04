# Blockers Log

Append-only log of blockers encountered during implementation. See `.claude/rules/no-workaround.md`.

When an agent (or human) hits a blocker, append an entry below — never patch around the issue silently.

## Format

```
## YYYY-MM-DD — <ticket-id> — <one-line title>

- File : <path:line>
- Symptom : <exact error message or unexpected behavior>
- Why blocked : <what was tried, what fails, what is unknown>
- Suggested resolution : <new ADR? upstream issue? spec amendment? human decision needed?>
- Status : open | resolved (commit SHA / ticket ID)
```

## Entries

<!-- Append below this line. Most recent first. -->

_No blockers logged yet._
