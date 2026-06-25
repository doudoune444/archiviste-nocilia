// #320: Palette violet editorial — remap tokens.css + sync copie gateway.
//
// The aesthetic itself is not unit-testable; the contract that IS testable is
// the token map: every existing --color-* name is preserved, values are
// remapped to the chat-1-filet palette, the new tokens exist, --radius/--shadow
// are present, and the gateway parallel copy stays in sync value-for-value.

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const FRONTEND_TOKENS = resolve(__dirname, "../../src/styles/tokens.css");
const GATEWAY_TOKENS = resolve(
  __dirname,
  "../../../gateway/static/assets/styles.css",
);

function parseRootTokens(css: string): Record<string, string> {
  const rootMatch = css.match(/:root\s*\{([^}]*)\}/);
  if (!rootMatch) {
    throw new Error("no :root block found");
  }
  const tokens: Record<string, string> = {};
  for (const declaration of rootMatch[1].split(";")) {
    const [name, ...rest] = declaration.split(":");
    const key = name.trim();
    if (key.startsWith("--")) {
      tokens[key] = rest.join(":").trim();
    }
  }
  return tokens;
}

const REMAPPED_COLORS: Record<string, string> = {
  "--color-bg": "#131217",
  "--color-surface": "#1c1b22",
  "--color-border": "#2c2b34",
  "--color-text": "#ececf0",
  "--color-text-muted": "#8a8794",
  "--color-accent": "#9277e8",
  "--color-user-bg": "#131217",
  "--color-assistant-bg": "#1c1b22",
  "--color-error-text": "#e06666",
};

const NEW_COLOR_TOKENS: Record<string, string> = {
  "--color-text-soft": "#b6b4c0",
  "--color-line-strong": "#3a3845",
  "--color-accent-soft": "#272237",
  "--color-accent-ink": "#c2b2f5",
  "--color-icon-bg": "#5b4baa",
  "--color-icon-ink": "#ece8ff",
};

const NON_COLOR_TOKENS: Record<string, string> = {
  "--radius": "8px",
  "--shadow": "0 1px 2px rgba(0,0,0,.35), 0 8px 24px rgba(0,0,0,.28)",
};

const PRESERVED_NAMES = [
  "--color-bg",
  "--color-surface",
  "--color-border",
  "--color-text",
  "--color-text-muted",
  "--color-accent",
  "--color-user-bg",
  "--color-assistant-bg",
  "--color-error-bg",
  "--color-error-text",
  "--color-healthy",
  "--font-sans",
  "--font-mono",
];

describe("design tokens — violet editorial palette (#320)", () => {
  const frontend = parseRootTokens(readFileSync(FRONTEND_TOKENS, "utf8"));
  const gateway = parseRootTokens(readFileSync(GATEWAY_TOKENS, "utf8"));

  it("preserves every existing --color-*/--font-* variable name", () => {
    for (const name of PRESERVED_NAMES) {
      expect(frontend, `tokens.css must keep ${name}`).toHaveProperty(name);
    }
  });

  it("remaps the existing palette values to chat-1-filet", () => {
    for (const [name, value] of Object.entries(REMAPPED_COLORS)) {
      expect(frontend[name]).toBe(value);
    }
  });

  it("adds the six new color tokens", () => {
    for (const [name, value] of Object.entries(NEW_COLOR_TOKENS)) {
      expect(frontend[name]).toBe(value);
    }
  });

  it("adds the --radius and --shadow design-system tokens", () => {
    for (const [name, value] of Object.entries(NON_COLOR_TOKENS)) {
      expect(frontend[name]).toBe(value);
    }
  });

  it("keeps the gateway copy synced value-for-value", () => {
    const synced = {
      ...REMAPPED_COLORS,
      ...NEW_COLOR_TOKENS,
      ...NON_COLOR_TOKENS,
    };
    for (const [name, value] of Object.entries(synced)) {
      expect(gateway[name], `gateway styles.css ${name}`).toBe(value);
    }
  });
});
