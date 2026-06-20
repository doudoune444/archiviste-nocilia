# ADR 0013 — Markdown sanitize renderer for LLM output

- Status: accepted
- Date: 2026-06-20
- Decider: Doudoune

## Context

LLM output from the assistant reaches the browser as raw text. Rendering it as
plain text (`white-space: pre-wrap`) was the CHAT-002 default — safe but unable
to surface headings, lists, emphasis, or fenced code.

CHAT-003 adds Markdown rendering. The XSS boundary is now critical:
`security.md §Output sanitization` explicitly requires:
- LLM output rendered to web: HTML-escaped; never raw HTML injection.
- Links: scheme allowlist (`http`, `https`, `mailto`); `javascript:` and others stripped.
- Code blocks: `<pre><code>` only; no auto-execute.

Two npm packages — `react-markdown@9` and `rehype-sanitize@6` — were already
declared in `package.json` (added during ADR-0012 frontend planning). No new
dependency is introduced by this ticket; this ADR documents their use contract.

## Decision

Build `AssistantAnswer` (`src/components/assistant-answer/`) using:

- **`react-markdown`** to convert Markdown source to a React element tree via
  the unified/remark/rehype pipeline — no `innerHTML`, no `eval`.
- **`rehype-sanitize`** (GitHub-style `defaultSchema`) as the sanitization layer,
  with the `protocols.href` allowlist tightened to `["http", "https", "mailto"]`.
  All other schemes — including `javascript:`, `vbscript:`, `data:` — are stripped.
- **`skipHtml: true`** on `react-markdown` to prevent raw HTML blocks embedded
  in Markdown from reaching the hast tree at all (defence-in-depth).
- Fenced code blocks render as `<pre><code>` via the default unified pipeline —
  the browser never executes them.

The schema is defined as a module-level constant (not inline per render) to
avoid allocation on every re-render.

## Consequences

Positive:
- LLM answers render rich Markdown (headings, lists, emphasis, fenced code).
- XSS boundary is explicit and covered by unit tests (`vitest`): script tags,
  `javascript:` links, `onerror` handlers, and raw embedded HTML are all tested.
- `mode` chip and citation count are surfaced when present.

Negative / accepted trade-offs:
- `react-markdown` + `rehype-sanitize` add parse overhead per committed message.
  Acceptable: rendering is post-stream (committed answers only), not on every token.
- `skipHtml: true` strips any intentional HTML inside the LLM's Markdown.
  Acceptable: LLM output should never rely on raw HTML to be readable.

## References

- `security.md §Output sanitization`, A03 (injection), A05 (misconfiguration)
- `src/components/assistant-answer/AssistantAnswer.tsx`
- `tests/unit/assistant-answer.test.tsx`
- `docs/adr/0012-frontend-nextjs-bff.md` — `react-markdown`/`rehype-sanitize` deps
