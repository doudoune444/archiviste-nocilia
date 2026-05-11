# Review (fresh-eyes, independent) — ING-010

## Verdict
REQUEST_CHANGES

## Context
Independent adversarial review with no prior context of `specs/reviews/ING-010.md`. Ran `cd scripts && uv run ruff check . && uv run mypy . && uv run pytest -v` — all green (81 tests pass, mypy strict clean, ruff clean once `.ruff_cache/` invalidated).

## Findings

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `scripts/gdrive_export/slugify.py:23-24` | HIGH | AC-3 idempotence broken — cap-then-no-strip | Pipeline strips `-` **before** the 80-char cap. If the cap truncates mid-hyphen-run, the result ends in `-`. Reproducer: `slugify("a"*79 + " "*5 + "b", "abcdef12")` returns `"a"*79 + "-"` (length 80). Re-applying slugify strips the trailing hyphen, yielding length 79 → `slugify(slugify(x)) != slugify(x)`. AC-3 explicitly mandates idempotence and the test `_SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]*[a-z0-9]$\|^[a-z0-9]$"` would reject the trailing-hyphen output — hypothesis just didn't shrink to this shape (random text rarely produces it). | Re-strip `-` after the cap: `capped = stripped[:MAX_LEN].strip("-")`. Add an explicit regression test for the truncation-at-hyphen case. |
| `scripts/tests/conftest.py:11-13` | MED | AC-14 guard regex bypassable | The pattern `r"(googleapiclient\|google\.auth\|httplib2\|requests\.\|httpx\.)"` only matches `requests.<x>` and `httpx.<x>` — i.e. attribute access. A future contributor writing `from requests import get` or `import httpx as h` would slip past the guard (`import requests` / `import httpx` plain are also missed). Spec AC-14 supplies the regex verbatim, so the implementation is spec-compliant, but the spec is weak. | Replace with `r"\b(googleapiclient\|google\.auth\|httplib2\|requests\|httpx)\b"` and write a self-test (synthetic file in a tmp dir) that asserts the guard catches all three forms. |
| `scripts/gdrive_export/state.py:78-81` | LOW | `.tmp` file leak on crash + not gitignored | If `os.replace` raises, the partially-written `<state>.tmp` stays on disk. `.gitignore` only excludes `scripts/.gdrive_state.json.bak`, not `scripts/.gdrive_state.json.tmp`. A future ING-013 run that crashes mid-write produces an orphan `.tmp` file a developer could accidentally `git add`. | `try / finally: tmp_path.unlink(missing_ok=True)` around the replace. Extend `.gitignore` line 91-92 with `scripts/.gdrive_state.json.tmp`. |
| `scripts/gdrive_export/rename.py:14-19` | LOW | TOCTOU between `exists()` and `rename()` | `if new_path.exists(): raise FileExistsError` then `old_path.rename(new_path)`. Between the check and the call another process can create `new_path`; on POSIX `Path.rename` silently overwrites it (Windows raises). Offline tool so risk is theoretical, but the FileExistsError invariant from AC-8 isn't actually guaranteed. | Use `os.link(old_path, new_path); old_path.unlink()` or `os.rename` after `os.open(new_path, O_CREAT \| O_EXCL)` reservation. Or document the TOCTOU and accept it given offline scope. |
| `scripts/gdrive_export/paths.py` (no test) | LOW | Adversarial `drive_file_id` and `ext` not tested | `drive_file_id[:8]` is concatenated into the filename stem and `ext` is concatenated directly. A `drive_file_id` of `"../../../"` produces a candidate that on POSIX contains literal `/` separators; the `_assert_under_root` guard **does** catch it (defended in depth), but no test exercises this path. Same for `ext=".md/../../etc"`. | Add `test_malicious_drive_file_id_caught` and `test_malicious_ext_caught` asserting `ValueError` from `_assert_under_root`. |
| `scripts/gdrive_export/frontmatter_merge.py:73-83` (also `tests/test_frontmatter_merge.py:80-87`) | LOW | YAML 1.1 boolean coercion silently corrupts user-data | `yaml.safe_load("tags: [yes, no]")` returns `[True, False]`. A user tag literally named `"yes"` becomes `True` on round-trip. The test acknowledges this as "expected behavior" (line 85-86). Spec AC-6 says custom keys must be "préservée intacte" — `[yes, no]` → `[True, False]` is not intact. | Either (a) move to `ruamel.yaml` with YAML 1.2 (needs ADR), or (b) document the limitation in the function docstring and have ING-013 reject loading non-string tag values, or (c) use a custom resolver that strips the implicit bool resolver. |
| `scripts/tests/test_rename.py:90-91` | LOW | AC-9 test inserts `git add -A` not specified by spec | Spec AC-9 reads: "git status --porcelain post-rename affiche bien une ligne `R`". The implementation runs `git add -A` **before** the porcelain check (line 90). Without staging, `git status --porcelain` would print ` D original.md\n?? renamed.md` — only `git diff --find-renames` would show `R`. The test is realistic (humans stage before committing) but stretches the spec wording. | Tighten the spec or document that the AC-9 invariant only holds after staging. |
| `docs/vision.md:52` | LOW | Out-of-plan file change | `Files to touch` in `specs/plans/ING-010.md` does not list `docs/vision.md`. The diff updates the phase-3 row to mention ING-010/011/012 split. Trivially justified by the four-way ticket split but not pre-approved by the plan. | Either add `docs/vision.md` to the plan retroactively, or revert this hunk and ship it in a separate `docs(vision)` PR. |
| `specs/acceptance/ING-011.md`, `ING-012.md`, `ING-013.md` (new) | LOW | Sister-ticket specs created in this PR | Plan `specs/plans/ING-010.md` only lists `specs/acceptance/ING-010.md` and `specs/plans/ING-010.md` under "Files to touch". Three additional acceptance specs (111 + 116 + 157 LOC) shipped here. CLAUDE.md flags `specs/` as humain-only — must verify these were human-authored, not agent-fabricated. | Confirm via commit author / log that ING-011/012/013 specs were human-written or human-approved before merge. If agent-generated, gate behind separate `/spec` runs. |
| diff size: 28 files, 2487 insertions, ~1058 src+test LOC | MED | `vertical-slice.md` ≤ 300 LOC rule violated | `vertical-slice.md` mandates "≤ 300 LOC diff (excluding migrations and generated files)". Actual: 303 LOC src + 755 LOC tests = 1058 LOC (excluding `uv.lock` 466 lines, `pyproject.toml`, configs, sister specs). Plan estimated ~340 LOC total, off by 3×. | Accept the overshoot with explicit waiver in the merge note (utilities library has high test-to-src ratio + 4 distinct modules), OR split tests across follow-ups. Pragmatically: tests are unavoidable for AC-15 / property coverage, this finding documents the rule-vs-reality gap rather than blocking. |

## Spec coverage

| AC | Status | Evidence |
|---|---|---|
| AC-1 | OK | `scripts/pyproject.toml`, `scripts/uv.lock`, `uv sync` + `uv run pytest` green (81 passed). |
| AC-2 | OK | `scripts/gdrive_export/slugify.py:9-26`, tests `test_slugify.py::TestSluggifyMatrix::*`. |
| AC-3 | **PARTIAL** | Idempotence broken on cap-induced trailing hyphen (HIGH finding above). Property tests pass only because hypothesis shrinking didn't find the case. |
| AC-4 | OK | `normalize.py`, `test_normalize.py::TestNormalizeBody::*` covers NULL bytes, CR, ligature, NFD→NFC. |
| AC-5 | OK | `state.py`, `test_state.py::TestRoundTrip` + `TestAtomicSave` + `TestComputeBodyHash`. Atomic write via `os.replace` is genuine (not stubbed). |
| AC-6 | OK with caveat | `frontmatter_merge.py`, `test_frontmatter_merge.py::TestReExport::*`. Custom key preservation tested but YAML 1.1 boolean coercion noted (LOW finding above). |
| AC-7 | OK | `paths.py::resolve_local_path`, collision-suffix path + `_assert_under_root`. Adversarial `drive_file_id`/`ext` not tested (LOW). |
| AC-8 | OK with TOCTOU caveat | `rename.py::rename_local_file`, no `subprocess` in prod ✓. |
| AC-9 | OK with caveat | `test_rename.py::test_git_detects_rename_as_r` uses real `git init` + `git add` + `git commit` + porcelain parse. `git add -A` insertion noted (LOW). |
| AC-10 | OK | `cd scripts && uv run ruff check . && uv run mypy .` — both exit 0. |
| AC-11 | OK | `.pre-commit-config.yaml` adds `scripts-ruff` + `scripts-mypy` local hooks scoped `^scripts/.*\.py$`. |
| AC-12 | OK | `.gitignore` adds `.gcp/`, `scripts/.gdrive_state.json`, `scripts/.gdrive_state.json.bak`. Missing `.tmp` (LOW). |
| AC-13 | OK | `docs/adr/0006-gdrive-api-client.md`, status `accepted`, date `2026-05-09`. |
| AC-14 | OK with weak guard | `conftest.py::pytest_sessionstart` fails session on Drive API import; regex weakness noted (MED). |
| AC-15 | OK | All public fns have docstrings; all ≤ 40 lines; mypy strict passes. |

## Property invariants
- Local property: `slugify(slugify(s, fid), fid) == slugify(s, fid)` — **REFUTED** by counter-example `"a"*79 + "     b"`. Hypothesis didn't find it; 200 max_examples insufficient. Add explicit regression test.
- Length cap ≤ 80 — holds.
- Alphabet `^[a-z0-9-]+$ \| ^file-[0-9a-f]{8}$` — holds (the strict `[a-z0-9][a-z0-9-]*[a-z0-9]` test pattern would refuse trailing `-`, but that pattern lives only in tests, not in spec).

## Security
- No secrets / credentials in diff ✓.
- No `subprocess` in production code ✓.
- No network calls ✓.
- Path traversal: `slugify` strips dot/slash components + `_assert_under_root` final guard ✓ (defense in depth holds even with malicious `drive_file_id`).
- Unsafe deserialization: `yaml.safe_load` only, never `yaml.load` ✓. No use of unsafe Python object serializers, no `eval` / `exec` / `subprocess.shell=True`.
- AC-14 import-firewall present but bypassable (MED above).
- ADR-0006 ratified; no Drive API imports in `gdrive_export/` (manually verified by grep).

## Out-of-scope changes
- `docs/vision.md` (LOW above).
- `specs/acceptance/ING-011.md`, `ING-012.md`, `ING-013.md` — three new sister specs (LOW above; need human-authorship confirmation).
- `CHANGELOG.md` `### Fixed` section listing MED-1/2/3 from a prior review pass — indicates this PR has already been through one review cycle; that's fine but the fresh-eyes review still finds an untouched HIGH (AC-3 idempotence) and additional LOWs the first pass missed.

## Lint / type / test status
- `uv run ruff check .` → All checks passed.
- `uv run mypy .` → Success: no issues found in 15 source files.
- `uv run pytest -v` → 81 passed in 1.94s.

## Bottom line
One real HIGH (slugify idempotence breaks on cap-at-hyphen, AC-3 is the headline AC of this ticket); one MED (vertical-slice LOC overshoot, plan estimate off by 3×); one MED (AC-14 guard regex from spec is bypassable). Several LOWs around defense-in-depth gaps and out-of-plan files. The implementer already shipped fixes for MED-1/2/3 from an earlier review, but missed the idempotence counter-example — likely because hypothesis didn't shrink to it.

Fix the HIGH (one-line: re-strip after cap + regression test), tighten the AC-14 regex, then ship.
