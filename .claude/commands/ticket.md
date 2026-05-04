---
description: Create a new ticket as an empty placeholder (use /spec for guided authoring)
argument-hint: <ID> "<short title>"
---

You are creating a new ticket placeholder. The user provides an ID and a short title.

> **Note**: for guided Socratic authoring, recommend `/spec <ID> "<brief>"` instead. `/ticket` only creates an empty stub for users who prefer to write the spec by hand.

Steps:

1. Verify the ID is unique by globbing `specs/acceptance/`. If a file `specs/acceptance/<ID>.md` exists, abort and tell the user.
2. Create `specs/acceptance/<ID>.md` with this template, filled in only with the title and ID:

```markdown
# <ID> — <Title>

## Context
<1-3 sentences. Why this ticket exists. What problem it solves. Author: humain.>

## Acceptance criteria

- AC-1: <observable behavior, testable via integration test>
- AC-2: <…>
- AC-3: <…>

## Non-goals
- <explicitly out of scope>

## Touch points (informative, not binding)
- gateway: <maybe>
- workers: <maybe>
- migrations: <maybe>
- specs/openapi: <maybe>

## Test oracle
- Integration: <scenario, expected outcome>
- Property (if applicable): <invariant ID from specs/properties.md>
- Eval (if RAG): <golden_qa subset>

## Effort estimate
S / M / L (target M = ≤ 1 day)

## Status
draft  ← change to "ready" only after humain review
```

3. **Stop**. Do **not** start planning or implementing. The humain fills in the criteria, then runs `/plan <ID>`.

Output to user:

```
Ticket <ID> placeholder created at specs/acceptance/<ID>.md
Next options:
- /spec <ID>   (recommended — guided authoring with spec-author agent)
- fill in by hand, then /plan <ID>
```
