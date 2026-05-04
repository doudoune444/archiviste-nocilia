# Clean code (language-agnostic)

Apply to all production code (Rust + Python).

## Naming
- No abbreviations (`user_count`, not `usr_cnt`).
- Booleans = predicates: `is_valid`, `has_token`, `should_retry`.
- Functions = verbs: `fetch_user`, not `user_data`.
- Types = nouns, no `Manager` / `Helper` / `Util` suffix unless justified.

## Functions
- ≤ 40 lines body.
- ≤ 4 parameters. Beyond → struct / dataclass.
- Cyclomatic complexity ≤ 10 (no deeply nested `if`/`match`).
- One return type, one purpose. SRP.

## Types
- One responsibility per type. If you struggle to name it, it does too much.
- Make illegal states unrepresentable (newtype, enum over bool, `NonEmpty<T>`).

## Don't
- No commented-out code. Delete.
- No comments explaining WHAT (`# increment counter`). Code is self-documenting.
- WHY comments only when non-obvious: workaround, invariant, perf reason, hidden constraint.
- No magic constants. Named const or read from config/env.
- No premature abstraction. Three similar lines beats a generic helper.
- No over-configurability. Hardcode until a second caller appears.

## DRY
- Semantic DRY only. Two functions sharing 5 lines but different intent ≠ duplication.
- Extract only when third occurrence appears.
