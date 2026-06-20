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
import styles from "./AssistantAnswer.module.css";

/** Only these three URL schemes are permitted in href/src attributes. */
const ALLOWED_SCHEMES = ["http", "https", "mailto"];

/**
 * Tightened schema: starts from rehype-sanitize defaultSchema (GitHub-style)
 * and overrides the protocol allowlist to strip every scheme not in
 * ALLOWED_SCHEMES. javascript:, vbscript:, data:, etc. are all rejected.
 */
const SANITIZE_SCHEMA: SanitizeSchema = {
  ...defaultSchema,
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
 * Renders a committed assistant answer as sanitized Markdown HTML.
 * Surfaces mode chip and citation count when present.
 */
export default function AssistantAnswer({
  text,
  mode,
  citations,
}: Props): React.ReactElement {
  const hasCitations = Array.isArray(citations) && citations.length > 0;

  return (
    <div data-testid="assistant-answer" className={styles.container}>
      <div className={styles.markdown}>
        <Markdown
          rehypePlugins={REHYPE_PLUGINS}
          skipHtml={true}
        >
          {text}
        </Markdown>
      </div>

      {(mode !== undefined || hasCitations) && (
        <footer className={styles.footer}>
          {mode !== undefined && (
            <span data-testid="mode-chip" className={styles.modeChip}>
              {mode}
            </span>
          )}
          {hasCitations && (
            <span
              data-testid="citations-footer"
              className={styles.citations}
            >
              {(citations as unknown[]).length} source
              {(citations as unknown[]).length > 1 ? "s" : ""}
            </span>
          )}
        </footer>
      )}
    </div>
  );
}
