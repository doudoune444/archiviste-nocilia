/**
 * Unit tests for AssistantAnswer — markdown-sanitize renderer (CHAT-003).
 *
 * AC: LLM output rendered to web must be HTML-escaped; never inject raw HTML.
 *     Link schemes restricted to http/https/mailto; other schemes stripped.
 *     Code blocks rendered as <pre><code>, never auto-executed.
 *     (security.md §Output sanitization, A03)
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import AssistantAnswer from "@/components/assistant-answer/AssistantAnswer";

// ---------------------------------------------------------------------------
// AC: <script> tags neutralized — not rendered as executable
// ---------------------------------------------------------------------------

describe("AssistantAnswer — script tag neutralization", () => {
  it("does not render a <script> element from markdown input", () => {
    // AC: <script> tags in LLM output must not reach the DOM as executable elements.
    render(
      <AssistantAnswer
        text={"Hello <script>alert('xss')</script> world"}
        citations={undefined}
      />
    );
    // script element must not be present in the document
    expect(document.querySelector("script")).toBeNull();
  });

  it("renders surrounding text even when script is stripped", () => {
    render(
      <AssistantAnswer
        text={"Before <script>evil()</script> after"}
        citations={undefined}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    expect(container.textContent).toContain("Before");
    expect(container.textContent).toContain("after");
  });
});

// ---------------------------------------------------------------------------
// AC: javascript: link scheme neutralized
// ---------------------------------------------------------------------------

describe("AssistantAnswer — javascript: link scheme neutralized", () => {
  it("does not render a javascript: href as a live link", () => {
    // AC: [text](javascript:alert(1)) must not produce an anchor with that href.
    // rehype-sanitize drops the href entirely (returns null) or removes the <a>.
    render(
      <AssistantAnswer
        text={"[click me](javascript:alert(1))"}
        citations={undefined}
      />
    );
    const anchors = document.querySelectorAll("a");
    for (const anchor of anchors) {
      const href = anchor.getAttribute("href");
      // href is either null (stripped) or must not be a javascript: URL
      if (href !== null) {
        expect(href).not.toMatch(/^javascript:/i);
      }
    }
  });

  it("does not render vbscript: href as a live link", () => {
    // AC: vbscript: is also a dangerous scheme — must be neutralized.
    render(
      <AssistantAnswer
        text={"[click](vbscript:MsgBox(1))"}
        citations={undefined}
      />
    );
    const anchors = document.querySelectorAll("a");
    for (const anchor of anchors) {
      const href = anchor.getAttribute("href");
      if (href !== null) {
        expect(href).not.toMatch(/^vbscript:/i);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// AC: raw embedded HTML (img onerror) does not pass through
// ---------------------------------------------------------------------------

describe("AssistantAnswer — raw HTML injection blocked", () => {
  it("does not render an img with an onerror attribute", () => {
    // AC: embedded HTML must not pass through — onerror event handlers stripped.
    render(
      <AssistantAnswer
        text={'<img src="x" onerror="alert(1)" />'}
        citations={undefined}
      />
    );
    const imgs = document.querySelectorAll("img");
    for (const img of imgs) {
      // onerror is an event handler; must not be present
      expect(img.getAttribute("onerror")).toBeNull();
    }
  });

  it("does not render inline event handlers injected via HTML", () => {
    // AC: onclick handlers from LLM output must not reach the DOM.
    render(
      <AssistantAnswer
        text={'<p onclick="steal()">text</p>'}
        citations={undefined}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    // The paragraph's onclick must not survive sanitization
    const paras = container.querySelectorAll("p[onclick]");
    expect(paras).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Schema-pinning tests — these cases FAIL if REHYPE_PLUGINS / SANITIZE_SCHEMA
// is removed from AssistantAnswer.tsx, proving the tightened allowlist is live.
//
// WHY these pin the schema:
//   react-markdown's defaultUrlTransform allows irc/ircs/xmpp (its safeProtocol
//   regex is /^(https?|ircs?|mailto|xmpp)$/i) and rehype-sanitize's own
//   defaultSchema also permits them (href: ['http','https','irc','ircs','mailto',
//   'xmpp']).  Only our tightened SANITIZE_SCHEMA — which overrides href to
//   ['http','https','mailto'] — strips them.  A future refactor that deletes
//   REHYPE_PLUGINS or reverts to defaultSchema would make these tests fail,
//   alerting the reviewer that the security contract has been broken.
// ---------------------------------------------------------------------------

describe("AssistantAnswer — schema-pinning: irc: stripped by tightened allowlist", () => {
  it("strips an irc: href that defaultUrlTransform and defaultSchema would allow", () => {
    // defaultUrlTransform passes irc:// (in its safeProtocol list).
    // defaultSchema.protocols.href includes 'irc' and 'ircs'.
    // Only the custom SANITIZE_SCHEMA (href: ['http','https','mailto']) blocks it.
    // This test FAILS if REHYPE_PLUGINS / the tightened schema is removed.
    render(
      <AssistantAnswer
        text={"[chat](irc://chat.freenode.net/nocilia)"}
        citations={undefined}
      />
    );
    const anchors = document.querySelectorAll("a");
    for (const anchor of anchors) {
      const href = anchor.getAttribute("href");
      if (href !== null) {
        expect(href).not.toMatch(/^ircs?:/i);
      }
    }
  });

  it("strips an xmpp: href that defaultUrlTransform and defaultSchema would allow", () => {
    // defaultUrlTransform passes xmpp: (in its safeProtocol list).
    // defaultSchema.protocols.href includes 'xmpp'.
    // Only the custom SANITIZE_SCHEMA (href: ['http','https','mailto']) blocks it.
    // This test FAILS if REHYPE_PLUGINS / the tightened schema is removed.
    render(
      <AssistantAnswer
        text={"[contact](xmpp:user@example.com)"}
        citations={undefined}
      />
    );
    const anchors = document.querySelectorAll("a");
    for (const anchor of anchors) {
      const href = anchor.getAttribute("href");
      if (href !== null) {
        expect(href).not.toMatch(/^xmpp:/i);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// AC: safe markdown preserved
// ---------------------------------------------------------------------------

describe("AssistantAnswer — safe markdown preserved", () => {
  it("renders emphasis", () => {
    // AC: standard markdown emphasis rendered.
    render(
      <AssistantAnswer
        text={"This is **bold** and _italic_."}
        citations={undefined}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    expect(container.querySelector("strong")).not.toBeNull();
    expect(container.querySelector("em")).not.toBeNull();
  });

  it("renders an unordered list", () => {
    // AC: lists rendered as markdown.
    render(
      <AssistantAnswer
        text={"- item one\n- item two"}
        citations={undefined}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    expect(container.querySelector("ul")).not.toBeNull();
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });

  it("renders fenced code as <pre><code>", () => {
    // AC: fenced code blocks render as <pre><code>, never auto-executed.
    render(
      <AssistantAnswer
        text={"```js\nconsole.log('hello');\n```"}
        citations={undefined}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    expect(container.querySelector("pre")).not.toBeNull();
    expect(container.querySelector("pre code")).not.toBeNull();
  });

  it("renders an http link as an anchor", () => {
    // AC: http links allowed through and rendered as <a>.
    render(
      <AssistantAnswer
        text={"[visit](https://example.com)"}
        citations={undefined}
      />
    );
    const link = screen.getByRole("link", { name: "visit" });
    expect(link).toHaveAttribute("href", "https://example.com");
  });

  it("renders a mailto link as an anchor", () => {
    // AC: mailto links allowed through.
    render(
      <AssistantAnswer
        text={"[email](mailto:test@example.com)"}
        citations={undefined}
      />
    );
    const link = screen.getByRole("link", { name: "email" });
    expect(link).toHaveAttribute("href", "mailto:test@example.com");
  });
});

// ---------------------------------------------------------------------------
// #326: the mode chip moved to the turn header (ChatForm layout layer).
// AssistantAnswer is body-only and must never render a mode-chip itself.
// ---------------------------------------------------------------------------

describe("AssistantAnswer — body only, no mode chip (#326)", () => {
  it("does not render a mode-chip (it now lives in the turn header)", () => {
    render(
      <AssistantAnswer
        text={"Hello."}
        citations={undefined}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    expect(container.querySelector("[data-testid='mode-chip']")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// #327: inline [source_path] markers → numbered superscript citations.
// citations carry { source_path, chunk_ords } (the worker payload shape).
// ---------------------------------------------------------------------------

describe("AssistantAnswer — superscript citations (#327)", () => {
  it("numbers distinct source_paths by first appearance; reuses the number for a repeated source", () => {
    // AC: text with [lore/a.md] ... [lore/b.md] ... [lore/a.md] + citations=[a,b]
    //     → two distinct numbers; a reuses #1 (two <sup.fn> for a, one for b).
    render(
      <AssistantAnswer
        text={"start [lore/a.md] middle [lore/b.md] end [lore/a.md]"}
        citations={[
          { source_path: "lore/a.md", chunk_ords: [0] },
          { source_path: "lore/b.md", chunk_ords: [1] },
        ]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    const sups = container.querySelectorAll("sup.fn");
    expect(sups).toHaveLength(3);
    const numbers = Array.from(sups, (s) => s.textContent);
    expect(numbers).toEqual(["1", "2", "1"]);
  });

  it("renders each superscript as an anchor pointing to #src-{n}", () => {
    // AC: clicking a superscript leads to #src-{n}.
    render(
      <AssistantAnswer
        text={"fact [lore/a.md]"}
        citations={[{ source_path: "lore/a.md", chunk_ords: [0] }]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    const sup = container.querySelector("sup.fn");
    expect(sup).not.toBeNull();
    const anchor = sup?.querySelector("a") ?? sup?.closest("a");
    expect(anchor?.getAttribute("href")).toBe("#src-1");
  });

  it("leaves an unknown [path] absent from citations rendered literally", () => {
    // AC: a [chemin/inconnu.md] not in citations stays literal — no fake superscript.
    render(
      <AssistantAnswer
        text={"known [lore/a.md] unknown [chemin/inconnu.md]"}
        citations={[{ source_path: "lore/a.md", chunk_ords: [0] }]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    expect(container.querySelectorAll("sup.fn")).toHaveLength(1);
    expect(container.textContent).toContain("[chemin/inconnu.md]");
  });

  it("does not turn a bracketed token into an executable link even when it looks like a scheme", () => {
    // AC (security regression): [javascript:alert(1)] in the text — even if it
    // somehow matched a citation — must never yield an executable link.
    render(
      <AssistantAnswer
        text={"danger [javascript:alert(1)]"}
        citations={[{ source_path: "javascript:alert(1)", chunk_ords: [0] }]}
      />
    );
    const anchors = document.querySelectorAll("a");
    for (const anchor of anchors) {
      const href = anchor.getAttribute("href");
      if (href !== null) {
        expect(href).not.toMatch(/^javascript:/i);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// #327: collapsible Sources panel (path-only) replaces citations-footer.
// ---------------------------------------------------------------------------

describe("AssistantAnswer — sources panel (#327)", () => {
  it("renders a <details> with one .src-item per citation, each with id=src-{n}, collapsed by default", () => {
    // AC: the panel renders N .src-item with id=src-{n}; collapsed (no `open`).
    render(
      <AssistantAnswer
        text={"answer [lore/a.md] and [lore/b.md]"}
        citations={[
          { source_path: "lore/a.md", chunk_ords: [0] },
          { source_path: "lore/b.md", chunk_ords: [1] },
        ]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    const details = container.querySelector("details.sources") as HTMLDetailsElement | null;
    expect(details).not.toBeNull();
    expect(details?.hasAttribute("open")).toBe(false);

    const items = container.querySelectorAll(".src-item");
    expect(items).toHaveLength(2);
    expect(container.querySelector("#src-1")).not.toBeNull();
    expect(container.querySelector("#src-2")).not.toBeNull();
    expect(container.textContent).toContain("lore/a.md");
    expect(container.textContent).toContain("lore/b.md");
  });

  it("summarises the source count as Sources (N)", () => {
    render(
      <AssistantAnswer
        text={"answer [lore/a.md]"}
        citations={[{ source_path: "lore/a.md", chunk_ords: [0] }]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    const summary = container.querySelector("details.sources summary");
    expect(summary?.textContent).toContain("Sources (1)");
  });

  it("renders no sources panel and no citations-footer when citations is empty", () => {
    // AC: citations-footer is replaced; empty citations → no panel at all.
    render(
      <AssistantAnswer
        text={"No sources."}
        citations={[]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    expect(container.querySelector("[data-testid='citations-footer']")).toBeNull();
    expect(container.querySelector("details.sources")).toBeNull();
  });

  it("does not escape a path-traversal-looking source_path into an executable link", () => {
    // Security: a hostile source_path is path-only text in the panel, never a live URL.
    render(
      <AssistantAnswer
        text={"x [evil.md]"}
        citations={[{ source_path: "evil.md", chunk_ords: [0] }]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    const item = container.querySelector(".src-item");
    expect(item?.querySelector("a[href^='http']")).toBeNull();
    expect(item?.querySelector("a[href^='javascript']")).toBeNull();
  });
});
