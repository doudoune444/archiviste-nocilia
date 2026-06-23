# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

## `prd` — parent PRDs are not buildable

`prd` marks a **parent PRD**: a tracking umbrella decomposed into vertical-slice child
issues by `/to-issues`. A PRD is **never** picked up by `/autobuild` and is **never**
built one-shot — only its child slices carry `ready-for-agent`. A PRD is done when all
its child slices are closed. PRDs therefore carry `prd` and **not** `ready-for-agent`;
`/autobuild` filters them out of the queue with `-label:prd`.

Edit the right-hand column to match whatever vocabulary you actually use.
