"use client";
/**
 * AssistantAnswer — sanitized Markdown renderer for committed LLM answers.
 *
 * Security boundary (security.md §Output sanitization, A03):
 * - react-markdown converts Markdown AST to React elements — no raw HTML eval.
 * - rehype-sanitize strips tags, attributes, and URL schemes not on the allowlist.
 * - Link href is constrained to http / https / mailto by the custom schema.
 *   javascript:, vbscript:, data:, and all other schemes are dropped.
 * - skipHtml=true prevents raw HTML embedded in markdown from reaching the DOM.
 * - Fenced code becomes <pre><code> — never auto-executed.
 *
 * WHY skipHtml + rehype-sanitize in tandem: skipHtml prevents remark from
 * passing raw HTML nodes into the rehype tree at all; rehype-sanitize is an
 * additional defence-in-depth layer for anything that may reach the hast.
 *
 * #327 — RAG citations:
 * - remarkCitations turns inline [source_path] markers into <sup class="fn">n</sup>
 *   anchors targeting the #src-{n} fragment. The fragment has no URL scheme, so
 *   it survives the tightened allowlist without reopening it; the only schema
 *   widening is allowing class on <sup>.
 * - A collapsed <details class="sources"> panel (built directly from citations,
 *   path-only) replaces the former citations-footer and hosts the #src-{n} ids.
 */

import Markdown from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import type { Options as SanitizeSchema } from "rehype-sanitize";
import { remarkCitations } from "./remarkCitations";
import styles from "./AssistantAnswer.module.css";

/** Only these three URL schemes are permitted in href/src attributes. */
const ALLOWED_SCHEMES = ["http", "https", "mailto"];

/**
 * Tightened schema: starts from rehype-sanitize defaultSchema (GitHub-style)
 * and overrides the protocol allowlist to strip every scheme not in
 * ALLOWED_SCHEMES. javascript:, vbscript:, data:, etc. are all rejected.
 *
 * #327: allow `class` on <sup> so the citation superscript keeps its `fn`
 * class. The URL scheme allowlist is left untouched — #src-{n} fragments carry
 * no scheme and pass regardless.
 */
const SANITIZE_SCHEMA: SanitizeSchema = {
  ...defaultSchema,
  protocols: {
    ...defaultSchema.protocols,
    href: ALLOWED_SCHEMES,
    src: ["http", "https"],
    cite: ["http", "https"],
  },
  attributes: {
    ...defaultSchema.attributes,
    sup: [...(defaultSchema.attributes?.sup ?? []), "className"],
  },
};

/** Stable plugin tuple — defined outside the render fn to avoid re-creating on every render. */
const REHYPE_PLUGINS: [typeof rehypeSanitize, SanitizeSchema][] = [
  [rehypeSanitize, SANITIZE_SCHEMA],
];

const LINK_ICON = "\u{1F517}";

interface Props {
  text: string;
  citations: unknown[] | undefined;
}

/**
 * Extracts the non-empty `source_path` of a citation entry, or null. The panel
 * is path-only (#327), so `chunk_ords` are intentionally ignored. A malformed
 * entry yields null and is dropped, so it can never inject markup or break
 * numbering.
 */
function sourcePathOf(entry: unknown): string | null {
  if (typeof entry !== "object" || entry === null) return null;
  const sourcePath = (entry as Record<string, unknown>)["source_path"];
  if (typeof sourcePath !== "string" || sourcePath.length === 0) return null;
  return sourcePath;
}

/** Distinct source_paths in first-appearance order — drives both numbering and the panel. */
function distinctSources(citations: unknown[]): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const entry of citations) {
    const sourcePath = sourcePathOf(entry);
    if (sourcePath !== null && !seen.has(sourcePath)) {
      seen.add(sourcePath);
      ordered.push(sourcePath);
    }
  }
  return ordered;
}

/**
 * Renders a committed assistant answer body as sanitized Markdown HTML with
 * superscript citations and a collapsible path-only sources panel. The mode
 * chip lives in the turn header (ChatForm layout layer, #326), not here.
 */
export default function AssistantAnswer({
  text,
  citations,
}: Props): React.ReactElement {
  const sources = distinctSources(Array.isArray(citations) ? citations : []);

  const remarkPlugins: [typeof remarkCitations, readonly string[]][] = [
    [remarkCitations, sources],
  ];

  return (
    <div data-testid="assistant-answer" className={styles.container}>
      <div className={styles.markdown}>
        <Markdown
          remarkPlugins={remarkPlugins}
          rehypePlugins={REHYPE_PLUGINS}
          skipHtml={true}
        >
          {text}
        </Markdown>
      </div>

      {sources.length > 0 && (
        <details className="sources">
          <summary>Sources ({sources.length})</summary>
          <ul className="src-list">
            {sources.map((sourcePath, index) => (
              <li key={sourcePath} id={`src-${index + 1}`} className="src-item">
                <span className="src-icon" aria-hidden="true">
                  {LINK_ICON}
                </span>
                <span className="src-path">{sourcePath}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
