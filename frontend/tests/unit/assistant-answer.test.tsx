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
        mode={undefined}
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
        mode={undefined}
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
        mode={undefined}
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
        mode={undefined}
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
        mode={undefined}
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
        mode={undefined}
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
        mode={undefined}
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
        mode={undefined}
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
        mode={undefined}
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
        mode={undefined}
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
        mode={undefined}
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
        mode={undefined}
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
        mode={undefined}
        citations={undefined}
      />
    );
    const link = screen.getByRole("link", { name: "email" });
    expect(link).toHaveAttribute("href", "mailto:test@example.com");
  });
});

// ---------------------------------------------------------------------------
// AC: mode chip rendered when provided
// ---------------------------------------------------------------------------

describe("AssistantAnswer — mode chip", () => {
  it("renders a mode chip when mode is provided", () => {
    // AC: mode surfaced where available.
    render(
      <AssistantAnswer
        text={"Hello."}
        mode={"canon"}
        citations={undefined}
      />
    );
    expect(screen.getByText("canon")).toBeInTheDocument();
  });

  it("does not render a mode chip when mode is undefined", () => {
    render(
      <AssistantAnswer
        text={"Hello."}
        mode={undefined}
        citations={undefined}
      />
    );
    // No chip element should be present
    const container = screen.getByTestId("assistant-answer");
    const chip = container.querySelector("[data-testid='mode-chip']");
    expect(chip).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC (#327): inline [source_path] markers → superscript citations
// ---------------------------------------------------------------------------

const citation = (sourcePath: string) => ({
  source_path: sourcePath,
  chunk_ords: [0],
});

describe("AssistantAnswer — superscript citations", () => {
  it("numbers distinct sources by first appearance and reuses the number for repeats", () => {
    // AC: text "[lore/a.md] ... [lore/b.md] ... [lore/a.md]" with citations=[a,b]
    // → a=1, b=2; two <sup class="fn"> for a, one for b.
    render(
      <AssistantAnswer
        text={
          "Intro [lore/a.md] middle [lore/b.md] outro [lore/a.md] end."
        }
        mode={undefined}
        citations={[citation("lore/a.md"), citation("lore/b.md")]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    const sups = container.querySelectorAll("sup.fn");
    const numbers = Array.from(sups).map((s) => s.textContent);
    expect(numbers).toEqual(["1", "2", "1"]);
  });

  it("does not render the raw [source_path] marker once converted", () => {
    render(
      <AssistantAnswer
        text={"Fact [lore/a.md] done."}
        mode={undefined}
        citations={[citation("lore/a.md")]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    expect(container.textContent).not.toContain("[lore/a.md]");
    expect(container.querySelector("sup.fn")).not.toBeNull();
  });

  it("leaves an unknown [path] absent from citations rendered literally", () => {
    // AC: a [chemin/inconnu.md] not in citations stays literal — no fake sup.
    render(
      <AssistantAnswer
        text={"Known [lore/a.md] but unknown [chemin/inconnu.md]."}
        mode={undefined}
        citations={[citation("lore/a.md")]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    expect(container.textContent).toContain("[chemin/inconnu.md]");
    const sups = container.querySelectorAll("sup.fn");
    expect(sups).toHaveLength(1);
  });

  it("links each superscript to the internal #src-{n} anchor", () => {
    // AC: clicking a superscript leads to #src-{n}.
    render(
      <AssistantAnswer
        text={"Fact [lore/a.md] then [lore/b.md]."}
        mode={undefined}
        citations={[citation("lore/a.md"), citation("lore/b.md")]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    const anchors = container.querySelectorAll("a[href='#src-1'] sup.fn");
    expect(anchors).toHaveLength(1);
    const anchorsTwo = container.querySelectorAll("a[href='#src-2'] sup.fn");
    expect(anchorsTwo).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// AC (#327): collapsible Sources panel (path-only)
// ---------------------------------------------------------------------------

describe("AssistantAnswer — sources panel", () => {
  it("renders a <details> with one .src-item per citation and #src-{n} ids", () => {
    render(
      <AssistantAnswer
        text={"Answer [lore/a.md] [lore/b.md]."}
        mode={undefined}
        citations={[citation("lore/a.md"), citation("lore/b.md")]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    const items = container.querySelectorAll(".src-item");
    expect(items).toHaveLength(2);
    expect(container.querySelector("#src-1")).not.toBeNull();
    expect(container.querySelector("#src-2")).not.toBeNull();
  });

  it("labels the summary with the source count", () => {
    render(
      <AssistantAnswer
        text={"Answer [lore/a.md] [lore/b.md]."}
        mode={undefined}
        citations={[citation("lore/a.md"), citation("lore/b.md")]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    const summary = container.querySelector("details > summary");
    expect(summary?.textContent).toContain("Sources");
    expect(summary?.textContent).toContain("2");
  });

  it("renders the panel collapsed by default (no open attribute)", () => {
    render(
      <AssistantAnswer
        text={"Answer [lore/a.md]."}
        mode={undefined}
        citations={[citation("lore/a.md")]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    expect(details?.hasAttribute("open")).toBe(false);
  });

  it("shows the source paths verbatim in the panel", () => {
    render(
      <AssistantAnswer
        text={"Answer [lore/a.md]."}
        mode={undefined}
        citations={[citation("lore/a.md")]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    expect(container.querySelector(".src-item")?.textContent).toContain(
      "lore/a.md"
    );
  });

  it("does not render the sources panel when citations is empty", () => {
    render(
      <AssistantAnswer text={"No sources."} mode={undefined} citations={[]} />
    );
    const container = screen.getByTestId("assistant-answer");
    expect(container.querySelector("details")).toBeNull();
  });

  it("no longer renders the legacy citations-footer", () => {
    render(
      <AssistantAnswer
        text={"Answer [lore/a.md]."}
        mode={undefined}
        citations={[citation("lore/a.md")]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    expect(
      container.querySelector("[data-testid='citations-footer']")
    ).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC (#327): citation conversion is sanitization-safe
// ---------------------------------------------------------------------------

describe("AssistantAnswer — citation security regression", () => {
  it("does not turn a [javascript:...] marker into an executable link", () => {
    // AC: [javascript:alert(1)] must not produce an executable link.
    render(
      <AssistantAnswer
        text={"Danger [javascript:alert(1)] here."}
        mode={undefined}
        citations={[citation("lore/a.md")]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    const anchors = container.querySelectorAll("a");
    for (const anchor of anchors) {
      const href = anchor.getAttribute("href");
      if (href !== null) {
        expect(href).not.toMatch(/^javascript:/i);
      }
    }
    // The marker is not in citations → stays literal, no sup produced for it.
    expect(container.querySelectorAll("sup.fn")).toHaveLength(0);
  });

  it("keeps anchor hrefs limited to the internal #src fragment", () => {
    // The only anchors the plugin introduces point at internal fragments.
    render(
      <AssistantAnswer
        text={"Fact [lore/a.md]."}
        mode={undefined}
        citations={[citation("lore/a.md")]}
      />
    );
    const container = screen.getByTestId("assistant-answer");
    const sup = container.querySelector("sup.fn");
    const anchor = sup?.closest("a");
    expect(anchor?.getAttribute("href")).toBe("#src-1");
  });
});
