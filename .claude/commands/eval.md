---
description: Run RAG quality eval (Ragas) against the golden Q/A set via eval-runner
argument-hint: [<ID>]
---

The user wants to run RAG quality evals. Optional argument: ticket ID for labeling.

Pre-flight (abort with reason on any fail):

1. Extract ticket ID: if `$ARGUMENTS` non-empty, must match `^[A-Z]+-[0-9]+$`. Otherwise use `adhoc`.
2. Workers reachable: `curl -sf http://localhost:8000/healthz` (timeout 3s). If fail → `docker compose up -d` first.
3. `specs/golden_qa.jsonl` exists and is non-empty.
4. `eval/baseline.json` exists. If not, tell user to seed baseline before any eval comparison.

Delegate to the `eval-runner` sub-agent with this prompt:

> Run RAG quality eval per your agent definition. Set: `specs/golden_qa.jsonl`. Output: `eval/runs/${TICKET_ID}-$(date +%Y%m%d-%H%M).json` where TICKET_ID = `$ARGUMENTS` or `adhoc`. Compare to `eval/baseline.json`. Commit run output: `chore(eval): ${TICKET_ID} Ragas run <PASS|BLOCK>`. Report verdict.

After eval-runner returns:

1. Verify the run was committed: last commit subject matches `chore(eval): * Ragas run *`. If not, commit:
   ```bash
   git add eval/runs/
   git commit -m "chore(eval): ${TICKET_ID} Ragas run <X>"
   ```
2. Surface verdict + failing samples to user.
3. If verdict is BLOCK: do NOT auto-fix. Investigate via `/debug` if needed, or revert offending change.
