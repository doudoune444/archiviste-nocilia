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
 */

import Markdown from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import type { Options as SanitizeSchema } from "rehype-sanitize";
import {
  citationNumbering,
  extractSourcePaths,
  remarkCitations,
} from "./remark-citations";
import styles from "./AssistantAnswer.module.css";

/** Only these three URL schemes are permitted in href/src attributes. */
const ALLOWED_SCHEMES = ["http", "https", "mailto"];

/**
 * Tightened schema: starts from rehype-sanitize defaultSchema (GitHub-style)
 * and overrides the protocol allowlist to strip every scheme not in
 * ALLOWED_SCHEMES. javascript:, vbscript:, data:, etc. are all rejected.
 *
 * Citation extension (#327): allow the <sup> element and a `className` on it so
 * the remarkCitations-emitted superscript survives sanitisation. No URL scheme
 * is reopened — the citation anchors are internal fragments (#src-{n}), which
 * carry no protocol and so pass the unchanged `href` allowlist.
 */
const SANITIZE_SCHEMA: SanitizeSchema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "sup"],
  attributes: {
    ...defaultSchema.attributes,
    sup: ["className"],
  },
  protocols: {
    ...defaultSchema.protocols,
    href: ALLOWED_SCHEMES,
    src: ["http", "https"],
    cite: ["http", "https"],
  },
};

/** Stable plugin tuple — defined outside the render fn to avoid re-creating on every render. */
const REHYPE_PLUGINS: [typeof rehypeSanitize, SanitizeSchema][] = [
  [rehypeSanitize, SANITIZE_SCHEMA],
];

interface Props {
  text: string;
  mode: string | undefined;
  citations: unknown[] | undefined;
}

/**
 * Renders a committed assistant answer as sanitized Markdown HTML, converting
 * inline `[source_path]` markers to numbered superscript citations and listing
 * the cited documents (path-only) in a collapsible Sources panel.
 */
export default function AssistantAnswer({
  text,
  mode,
  citations,
}: Props): React.ReactElement {
  const sourcePaths = extractSourcePaths(citations);
  const numbering = citationNumbering(citations);
  const remarkPlugins: [ReturnType<typeof remarkCitations>][] = [
    [remarkCitations(numbering)],
  ];

  return (
    <div data-testid="assistant-answer" className={styles.container}>
      {mode !== undefined && (
        <span data-testid="mode-chip" className={styles.modeChip}>
          {mode}
        </span>
      )}

      <div className={styles.markdown}>
        <Markdown
          remarkPlugins={remarkPlugins}
          rehypePlugins={REHYPE_PLUGINS}
          skipHtml={true}
        >
          {text}
        </Markdown>
      </div>

      {sourcePaths.length > 0 && (
        <details className={styles.sources}>
          <summary>Sources ({sourcePaths.length})</summary>
          <ul className={styles.srcList}>
            {sourcePaths.map((sourcePath, index) => (
              <li
                key={sourcePath}
                id={`src-${index + 1}`}
                className={`${styles.srcItem} src-item`}
              >
                <span className={styles.srcIcon} aria-hidden="true">
                  🔗
                </span>
                <span className={styles.srcPath}>{sourcePath}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
