---
description: Generate implementation plan for a ticket via the architect sub-agent
argument-hint: <ID>
---

The user wants a plan for ticket `$ARGUMENTS`.

Steps:

0. Validate `$ARGUMENTS` strictly matches `^[A-Z]+-[0-9]+$`. Otherwise abort with reason. No exceptions.
1. Verify `specs/acceptance/$ARGUMENTS.md` exists and its `Status:` field is `ready` (not `draft`). If not, abort.
2. Verify `specs/plans/$ARGUMENTS.md` does NOT already exist. If it does, ask the user if they want to overwrite.
3. Delegate to the `architect` sub-agent with this prompt:

> Read `specs/acceptance/$ARGUMENTS.md`, then map the codebase (Glob/Grep) to identify all files relevant to this ticket. Then produce `specs/plans/$ARGUMENTS.md` following the template in your agent definition. Stop after writing the plan — do not implement anything.

4. After the architect returns, commit the plan:
   ```bash
   git add specs/plans/$ARGUMENTS.md
   git commit -m "docs(plan): $ARGUMENTS"
   ```
5. **Stop.** Tell the user:

```
Plan written + committed: specs/plans/$ARGUMENTS.md
Review it. If approved, run /impl $ARGUMENTS.
If you need changes, edit the plan directly or re-run /plan (will produce a new commit).
```
