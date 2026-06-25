/**
 * remarkCitations — mdast transformer that turns inline `[source_path]` markers
 * into numbered superscript citations linking to the in-page Sources panel.
 *
 * WHY a remark plugin (not a string `replace`): operating on the mdast text
 * nodes leaves block structure and code spans intact and keeps the output
 * inside the react-markdown → rehype-sanitize pipeline. A raw string replace
 * would inject markup ahead of sanitisation and could corrupt fenced code.
 *
 * Security: the only nodes introduced are internal-fragment links (`#src-{n}`).
 * No external URL scheme is produced, so the tightened `ALLOWED_SCHEMES`
 * sanitisation allowlist stays untouched (security.md §Output sanitization).
 * Markers whose path is absent from `citations` are left verbatim — an unknown
 * `[javascript:...]` therefore never becomes a link.
 */

import { visit, CONTINUE, SKIP } from "unist-util-visit";
import type { Root, Text, PhrasingContent } from "mdast";

const CITATION_PATTERN = /\[([^\]\s][^\]]*)\]/g;

/** Maps a source_path to its 1-based citation number (first-appearance order). */
export type CitationNumbering = ReadonlyMap<string, number>;

interface SourceLikeCitation {
  source_path: string;
}

function isSourceCitation(value: unknown): value is SourceLikeCitation {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { source_path?: unknown }).source_path === "string"
  );
}

/**
 * Extracts the ordered list of distinct source paths from a raw citations array.
 * Non-conforming entries are skipped; order follows the citations array.
 */
export function extractSourcePaths(citations: unknown[] | undefined): string[] {
  if (!Array.isArray(citations)) return [];
  const ordered: string[] = [];
  const seen = new Set<string>();
  for (const entry of citations) {
    if (!isSourceCitation(entry)) continue;
    const path = entry.source_path;
    if (seen.has(path)) continue;
    seen.add(path);
    ordered.push(path);
  }
  return ordered;
}

function buildNumbering(sourcePaths: string[]): Map<string, number> {
  const numbering = new Map<string, number>();
  sourcePaths.forEach((path, index) => numbering.set(path, index + 1));
  return numbering;
}

function makeSuperscriptLink(sourceNumber: number): PhrasingContent {
  return {
    type: "link",
    url: `#src-${sourceNumber}`,
    children: [
      {
        type: "text",
        value: String(sourceNumber),
        data: { hName: "sup", hProperties: { className: ["fn"] } },
      } as Text,
    ],
  };
}

function splitTextNode(
  node: Text,
  numbering: CitationNumbering
): PhrasingContent[] | null {
  CITATION_PATTERN.lastIndex = 0;
  const replacements: PhrasingContent[] = [];
  let lastIndex = 0;
  let changed = false;
  let match: RegExpExecArray | null;

  while ((match = CITATION_PATTERN.exec(node.value)) !== null) {
    const sourceNumber = numbering.get(match[1]);
    if (sourceNumber === undefined) continue;
    if (match.index > lastIndex) {
      replacements.push({ type: "text", value: node.value.slice(lastIndex, match.index) });
    }
    replacements.push(makeSuperscriptLink(sourceNumber));
    lastIndex = match.index + match[0].length;
    changed = true;
  }

  if (!changed) return null;
  if (lastIndex < node.value.length) {
    replacements.push({ type: "text", value: node.value.slice(lastIndex) });
  }
  return replacements;
}

/**
 * remark plugin factory. Pass the numbering derived from `citations`; returns
 * a unified attacher whose transformer replaces each known `[source_path]`
 * text run with a superscript link node and leaves unknown markers untouched.
 */
export function remarkCitations(numbering: CitationNumbering) {
  return function attacher() {
    return function transform(tree: Root): void {
      if (numbering.size === 0) return;
      visit(tree, "text", (node: Text, index, parent) => {
        if (parent === undefined || index === undefined) return CONTINUE;
        const replacements = splitTextNode(node, numbering);
        if (replacements === null) return CONTINUE;
        const children = parent.children as PhrasingContent[];
        children.splice(index, 1, ...replacements);
        const nextIndex = index + replacements.length;
        return nextIndex < children.length ? [SKIP, nextIndex] : [SKIP];
      });
    };
  };
}

export function citationNumbering(
  citations: unknown[] | undefined
): Map<string, number> {
  return buildNumbering(extractSourcePaths(citations));
}
