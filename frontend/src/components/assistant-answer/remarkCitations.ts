/**
 * remarkCitations — mdast transform that turns inline [source_path] markers
 * (emitted verbatim by the LLM) into numbered superscript citations.
 *
 * WHY a remark plugin, not a string replace: a pre-render string replace would
 * corrupt block markdown and bypass the rehype-sanitize pass. Operating on the
 * mdast `text` nodes keeps the whole sanitisation pipeline intact.
 *
 * Behaviour (#327):
 * - Only [path] tokens whose path matches an entry in `knownSources` become
 *   superscripts. Any other [token] is left as literal text.
 * - Numbering follows first appearance of *distinct* paths; the same path
 *   reuses its number.
 * - The superscript is emitted as an mdast `link` to the internal fragment
 *   `#src-{n}` — a same-document anchor with no URL scheme, so it survives
 *   rehype-sanitize without reopening the scheme allowlist. The link carries an
 *   hast `sup.fn` wrapper via `data.hName` / `data.hChildren`.
 */

import type { Root, Text, PhrasingContent } from "mdast";
import { visit } from "unist-util-visit";

const CITATION_PATTERN = /\[([^\]\s][^\]]*)\]/g;

/**
 * Builds the citation node for number `n`: an internal-fragment link
 * (#src-{n}) wrapping a <sup class="fn">n</sup>. The link renders as a normal
 * <a> (default hName) so the fragment anchor is clickable; the inner emphasis
 * node is re-tagged to <sup> with the `fn` class via hast hints.
 */
function buildSuperscript(citationNumber: number): PhrasingContent {
  return {
    type: "link",
    url: `#src-${citationNumber}`,
    children: [
      {
        type: "emphasis",
        children: [{ type: "text", value: String(citationNumber) }],
        data: {
          hName: "sup",
          hProperties: { className: ["fn"] },
        },
      },
    ],
  } as PhrasingContent;
}

/**
 * Splits a single text node's value into a sequence of phrasing nodes,
 * replacing known [path] markers with superscript links and keeping the rest
 * (including unknown markers) as literal text.
 */
function splitTextNode(
  value: string,
  numberFor: (sourcePath: string) => number | undefined
): PhrasingContent[] {
  const nodes: PhrasingContent[] = [];
  let lastIndex = 0;

  for (const match of value.matchAll(CITATION_PATTERN)) {
    const sourcePath = match[1];
    const citationNumber = numberFor(sourcePath);
    if (citationNumber === undefined) continue;

    const matchStart = match.index;
    if (matchStart > lastIndex) {
      nodes.push({ type: "text", value: value.slice(lastIndex, matchStart) });
    }
    nodes.push(buildSuperscript(citationNumber));
    lastIndex = matchStart + match[0].length;
  }

  if (lastIndex < value.length) {
    nodes.push({ type: "text", value: value.slice(lastIndex) });
  }
  return nodes;
}

/**
 * remark plugin factory. `knownSources` is the ordered list of distinct
 * source_paths (panel order) used both to gate which markers convert and to
 * assign the 1-based citation number.
 */
export function remarkCitations(knownSources: readonly string[]) {
  const numberByPath = new Map<string, number>();
  knownSources.forEach((sourcePath, index) => {
    if (!numberByPath.has(sourcePath)) {
      numberByPath.set(sourcePath, index + 1);
    }
  });

  const numberFor = (sourcePath: string): number | undefined =>
    numberByPath.get(sourcePath);

  return (tree: Root): void => {
    visit(tree, "text", (node: Text, index, parent) => {
      if (parent === undefined || index === undefined) return;

      const replacements = splitTextNode(node.value, numberFor);
      const hasSuperscript = replacements.some((n) => n.type === "link");
      if (!hasSuperscript) return;

      (parent.children as PhrasingContent[]).splice(index, 1, ...replacements);
    });
  };
}
