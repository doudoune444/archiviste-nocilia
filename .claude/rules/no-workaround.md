# No workaround

If you hit a blocker, **stop**. Do not contournes.

## Blocker examples
- Library/crate API missing or broken.
- External service returns unexpected shape.
- Type system fights what the spec asks.
- Platform mismatch (Windows vs Linux file paths, etc).
- Heavy dependency missing — would need new ADR.

## Protocol
1. Stop coding immediately.
2. Append entry to `docs/blockers.md`:
   ```
   ## <date> — <ticket ID> — <one-line title>
   - File: <path:line>
   - Symptom: <exact error or unexpected behavior>
   - Why blocked: <what you tried, what fails>
   - Suggested resolution: <new ADR? upstream issue? spec amendment?>
   ```
3. Report back to human. Wait for guidance. **Never** patch around.

## Forbidden
- `# type: ignore` to skip the problem.
- `unwrap()` "just for now".
- Hardcoded value to bypass broken code path.
- Catching + swallowing the error.
