---
description: Author or iterate on a ticket's acceptance specification (Socratic dialogue with spec-author agent)
argument-hint: <ID> "<initial brief>"  OR  <ID>  (to iterate on existing)
---

The user wants help authoring or iterating on a ticket's acceptance spec.

Parse arguments:
- If `$ARGUMENTS` contains an ID + a quoted brief: new spec authoring.
- If `$ARGUMENTS` is just an ID and `specs/acceptance/<ID>.md` exists: iteration mode.
- If `$ARGUMENTS` is empty: ask the user for an ID + brief.

Pre-flight:

0. Sanitize ID: extract first token. Must match `^[A-Z]+-[0-9]+$`. Otherwise abort with reason.
1. Verify `specs/acceptance/` directory exists. If not, create it (it's a project bootstrap moment).
2. If the user gave an ID for a new spec and the file already exists, confirm with the user whether to iterate or pick a different ID — don't overwrite.

Delegate to the `spec-author` sub-agent with this prompt:

> Author or iterate on the acceptance spec for ticket `$ARGUMENTS`. Follow your full workflow: orient (read CLAUDE.md, specs/README.md, existing specs for format), ask round-1 questions (max 5), draft v1, run the quality checklist, surface open questions, iterate with the human until checklist is green and human says ready. Never mark Status: ready without explicit human confirmation. Never propose implementations.

After the spec-author returns:

- If it asked questions, surface them verbatim to the user and stop. Wait for the user's next message before re-invoking.
- If it wrote a draft (`Status: draft`), commit it:
  ```bash
  git add specs/acceptance/<ID>.md
  git commit -m "docs(spec): <ID> draft"
  ```
  Tell user the file path and open-question count.
- If the user has confirmed `ready` and Status reflects that, commit:
  ```bash
  git add specs/acceptance/<ID>.md
  git commit -m "docs(spec): <ID> ready"
  ```
  Then tell the user: `Spec ready. Next: /plan <ID>.`

Never auto-progress to `/plan` — the human always validates the spec by triggering the next command themselves.
