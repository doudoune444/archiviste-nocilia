// #324: Sidebar — label de section en capitales accent, espacements, bloc compte.
//
// The aesthetic itself is not unit-testable; the contract that IS testable is
// the CSS declarations the acceptance criteria pin down (same idiom as the
// #320 design-tokens test): the history section label renders uppercase with
// the accent tint, the sidebar spacings reuse the chat-1-filet scale, and the
// account block stays a structured, bordered group.

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const HISTORY_CSS = resolve(
  __dirname,
  "../../src/components/conversation-history/ConversationHistory.module.css",
);
const SHELL_CSS = resolve(
  __dirname,
  "../../src/components/app-sidebar/SidebarShell.module.css",
);

function readRule(css: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  if (match === null) {
    throw new Error(`rule ${selector} not found`);
  }
  return match[1];
}

describe("sidebar habillage — section label (#324)", () => {
  const historyCss = readFileSync(HISTORY_CSS, "utf8");
  const heading = () => readRule(historyCss, ".sidebarHeading");

  it("renders the history section label in uppercase", () => {
    expect(heading()).toMatch(/text-transform:\s*uppercase/);
  });

  it("tints the history section label with the accent token", () => {
    expect(heading()).toMatch(/color:\s*var\(--color-accent\)/);
  });
});

describe("sidebar habillage — spacing scale (#324)", () => {
  const shellCss = readFileSync(SHELL_CSS, "utf8");
  const sidebar = () => readRule(shellCss, ".sidebar");

  it("reuses the chat-1-filet sidebar padding (10px) and gap (8px)", () => {
    expect(sidebar()).toMatch(/padding:\s*0\.625rem/);
    expect(sidebar()).toMatch(/gap:\s*0\.5rem/);
  });
});

describe("sidebar habillage — account block (#324)", () => {
  const shellCss = readFileSync(SHELL_CSS, "utf8");
  const accountBlock = () => readRule(shellCss, ".accountBlock");

  it("separates the account block with a top border", () => {
    expect(accountBlock()).toMatch(/border-top:[^;]*var\(--color-border\)/);
  });

  it("stacks the account block as a structured column", () => {
    expect(accountBlock()).toMatch(/flex-direction:\s*column/);
  });
});
