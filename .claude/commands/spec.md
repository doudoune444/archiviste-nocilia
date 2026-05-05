---
description: Author or iterate on a ticket's acceptance specification (Socratic dialogue with spec-author agent)
argument-hint: [ID] "<initial brief>"   (ID optional — omit it and spec-author proposes one)
---

The user wants help authoring or iterating on a ticket's acceptance spec.

Extract from `$ARGUMENTS`:
- An ID matching `^[A-Z]+-[0-9]+$` if present. Otherwise unknown — let spec-author propose one.
- The brief = remainder. If empty and ID is unknown, ask humain for a brief.

Delegate to the `spec-author` sub-agent. Prompt template:

> Author or iterate on the acceptance spec for the ticket described below.

Brief: <quoted brief>

Follow your full workflow: orient (read CLAUDE.md, specs/README.md, existing specs for format), ask questions (min 5), draft v1, run the quality checklist, surface open questions, iterate with the humain until checklist is green and humain says ready. Never mark Status: ready without explicit humain confirmation. Never propose implementations.

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
