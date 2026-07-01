/**
 * Re-hydration of a persisted assistant turn (#375, PRD #372).
 *
 * On reload from history the transcript row (GET /v1/conversations/{id}/messages)
 * carries only the raw persisted `content` — #374 stores it verbatim: the answer
 * body + inline `[source_path]` markers + the trailing `---SUIVI---` block. The
 * live-stream metadata (citations, followups) is NEVER persisted, so the client
 * reconstructs what it can from the string here, so a reloaded assistant turn
 * renders like the freshly-streamed one. `mode` is unrecoverable and stays unset.
 *
 * This mirrors the worker post-hoc parse (extract_followups + extract_citations in
 * generate/parser.py); the regexes below are kept in sync with that source of truth
 * (and with the streaming-time stripFollowupBlock in ChatForm).
 *
 * A09: never logs content — callers must not log the returned answer.
 */

/** One reconstructed citation. Path-only, matching the panel's `source_path` read. */
export interface PersistedCitation {
  source_path: string;
}

/** A persisted assistant turn split back into its renderable parts. */
export interface PersistedAnswer {
  text: string;
  followups: string[];
  citations: PersistedCitation[];
}

// First whole line that, ignoring surrounding spaces and -/*/_ runs, reduces to
// the token SUIVI. The whole-line anchor is critical: the French word "suivi"
// mid-sentence must never trigger a cut (#345).
const FOLLOWUP_MARKER_LINE = /^[ \t]*[-*_]*[ \t]*SUIVI[ \t]*[-*_]*[ \t]*$/im;
// A markdown horizontal rule the model may emit just before the marker line.
const TRAILING_RULE = /\n[ \t]*[-*_]{3,}[ \t]*$/;
// Leading bullet (-, *, •) + spaces on a follow-up line.
const FOLLOWUP_BULLET = /^[-*•]+[ \t]*/;
// Inline `[source_path]` marker; permissive like the worker's _CITATION_RE.
const CITATION_MARKER = /\[([^\]\s][^\]]*)\]/g;

/** Splits the answer body from the follow-up block, returning both. */
function splitFollowupBlock(content: string): {
  body: string;
  followups: string[];
} {
  const match = FOLLOWUP_MARKER_LINE.exec(content);
  if (match === null) {
    return { body: content.trimEnd(), followups: [] };
  }
  const body = content
    .slice(0, match.index)
    .trimEnd()
    .replace(TRAILING_RULE, "")
    .trimEnd();
  const block = content.slice(match.index + match[0].length);
  const followups: string[] = [];
  for (const rawLine of block.split(/\r?\n/)) {
    const line = rawLine.trim().replace(FOLLOWUP_BULLET, "").trim();
    if (line.length > 0) {
      followups.push(line);
    }
  }
  return { body, followups };
}

/** Distinct `[source_path]` markers (comma-groups split) in first-appearance order. */
function extractCitations(body: string): PersistedCitation[] {
  const seen = new Set<string>();
  const citations: PersistedCitation[] = [];
  for (const match of body.matchAll(CITATION_MARKER)) {
    for (const piece of match[1].split(",")) {
      const sourcePath = piece.trim();
      if (sourcePath.length > 0 && !seen.has(sourcePath)) {
        seen.add(sourcePath);
        citations.push({ source_path: sourcePath });
      }
    }
  }
  return citations;
}

/**
 * Parses a persisted assistant `content` string into its renderable parts:
 * the body (follow-up block removed), the follow-up questions, and the citations
 * derived from the body's inline `[source_path]` markers.
 */
export function parsePersistedAnswer(content: string): PersistedAnswer {
  const { body, followups } = splitFollowupBlock(content);
  return { text: body, followups, citations: extractCitations(body) };
}
