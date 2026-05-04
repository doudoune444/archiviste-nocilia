---
description: Implement a ticket via the implementer sub-agent (requires validated plan)
argument-hint: <ID>
---

The user wants to implement ticket `$ARGUMENTS`.

Pre-flight checks:

1. Verify `specs/acceptance/$ARGUMENTS.md` exists.
2. Verify `specs/plans/$ARGUMENTS.md` exists. If not: tell user to run `/plan $ARGUMENTS` first, abort.
3. Verify the working tree is clean (`git status --porcelain` returns nothing). If dirty, ask user to commit or stash first — but **never** run `git stash` yourself.
4. Verify a feature branch is checked out (not `main` and not `develop`). Branch name should match `feat/$ARGUMENTS-<short-slug>` or `hotfix/<slug>`. If on `main` or `develop`, instruct user to create a branch — **never** create one yourself with `git checkout -b`.

If all checks pass, delegate to the `implementer` sub-agent with this prompt:

> Implement ticket `$ARGUMENTS` per `specs/plans/$ARGUMENTS.md`. Follow the workflow in your agent definition: migration first, integration test red, implementation green, property test if applicable, full check pack, OpenAPI update if needed, CHANGELOG entry. Stop and report when done. Diff must stay ≤ 300 LOC excluding migrations.

After the implementer returns:

1. Run `git diff --stat` to show the user the change footprint.
2. Tell the user:

```
Implementation complete. Next:
- /review $ARGUMENTS  (run adversary review)
- /eval               (if RAG path touched)
- /ship $ARGUMENTS    (when both pass)
```
