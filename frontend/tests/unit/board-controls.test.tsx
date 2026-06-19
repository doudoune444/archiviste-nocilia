// AC BOARD-003: BoardControls client component pushes the correct URL when the
// user changes category or sort, and removes the category param on clear.
// These tests cover the gap identified in the adversarial review: the pure
// params.ts module is unit-tested, but the <select> handlers that drive the URL
// had zero executing coverage.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockPush = vi.fn<(url: string) => void>();

// Mutable ref so individual tests can inject different URLSearchParams.
let currentSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/board",
  useSearchParams: () => currentSearchParams,
}));

// CSS Modules are not processed by Vitest/jsdom — stub them out.
vi.mock(
  "@/components/category-filter/BoardControls.module.css",
  () => ({
    default: new Proxy(
      {},
      { get: (_target, prop) => String(prop) },
    ),
  }),
);

import { BoardControls } from "@/components/category-filter/BoardControls";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const CATEGORIES = ["lore", "personnages", "lieux"];

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("BoardControls — handleCategoryChange (AC BOARD-003)", () => {
  beforeEach(() => {
    mockPush.mockReset();
    currentSearchParams = new URLSearchParams();
  });

  // AC BOARD-003: selecting a category pushes ?category=<value> into the URL.
  it("pushes category=lore when user selects 'lore' from the category select", () => {
    render(<BoardControls availableCategories={CATEGORIES} />);
    const select = screen.getByTestId("category-select");
    fireEvent.change(select, { target: { value: "lore" } });

    expect(mockPush).toHaveBeenCalledOnce();
    const pushed = mockPush.mock.calls[0][0];
    const url = new URL(pushed, "http://localhost");
    expect(url.searchParams.get("category")).toBe("lore");
    // sort=priority is the default → omitted for clean URLs (params.ts contract)
    expect(url.searchParams.has("sort")).toBe(false);
  });

  // AC BOARD-003: selecting another category replaces the previous one.
  it("replaces an existing category param when a new category is selected", () => {
    currentSearchParams = new URLSearchParams("category=lieux");
    render(<BoardControls availableCategories={CATEGORIES} />);
    const select = screen.getByTestId("category-select");
    fireEvent.change(select, { target: { value: "personnages" } });

    expect(mockPush).toHaveBeenCalledOnce();
    const pushed = mockPush.mock.calls[0][0];
    const url = new URL(pushed, "http://localhost");
    expect(url.searchParams.get("category")).toBe("personnages");
  });

  // AC BOARD-003: selecting the blank option ("Toutes") removes category param.
  it("removes the category param when the user selects the blank 'Toutes' option", () => {
    currentSearchParams = new URLSearchParams("category=lore");
    render(<BoardControls availableCategories={CATEGORIES} />);
    const select = screen.getByTestId("category-select");
    fireEvent.change(select, { target: { value: "" } });

    expect(mockPush).toHaveBeenCalledOnce();
    const pushed = mockPush.mock.calls[0][0];
    const url = new URL(pushed, "http://localhost");
    expect(url.searchParams.has("category")).toBe(false);
  });
});

describe("BoardControls — handleSortChange (AC BOARD-003)", () => {
  beforeEach(() => {
    mockPush.mockReset();
    currentSearchParams = new URLSearchParams();
  });

  // AC BOARD-003: sort=date pushes ?sort=date (non-default → present in URL).
  it("pushes sort=date when user selects 'date' from the sort select", () => {
    render(<BoardControls availableCategories={CATEGORIES} />);
    const select = screen.getByTestId("sort-select");
    fireEvent.change(select, { target: { value: "date" } });

    expect(mockPush).toHaveBeenCalledOnce();
    const pushed = mockPush.mock.calls[0][0];
    const url = new URL(pushed, "http://localhost");
    expect(url.searchParams.get("sort")).toBe("date");
  });

  // AC BOARD-003: sort=priority (default) is omitted from the URL (clean URL).
  it("omits the sort param when sort is set back to the default 'priority'", () => {
    currentSearchParams = new URLSearchParams("sort=date");
    render(<BoardControls availableCategories={CATEGORIES} />);
    const select = screen.getByTestId("sort-select");
    fireEvent.change(select, { target: { value: "priority" } });

    expect(mockPush).toHaveBeenCalledOnce();
    const pushed = mockPush.mock.calls[0][0];
    const url = new URL(pushed, "http://localhost");
    expect(url.searchParams.has("sort")).toBe(false);
  });

  // AC BOARD-003: changing sort preserves an active category filter.
  it("preserves an active category filter when the sort changes", () => {
    currentSearchParams = new URLSearchParams("category=lore");
    render(<BoardControls availableCategories={CATEGORIES} />);
    const select = screen.getByTestId("sort-select");
    fireEvent.change(select, { target: { value: "date" } });

    expect(mockPush).toHaveBeenCalledOnce();
    const pushed = mockPush.mock.calls[0][0];
    const url = new URL(pushed, "http://localhost");
    expect(url.searchParams.get("category")).toBe("lore");
    expect(url.searchParams.get("sort")).toBe("date");
  });
});

describe("BoardControls — handleClearCategory (AC BOARD-003)", () => {
  beforeEach(() => {
    mockPush.mockReset();
    currentSearchParams = new URLSearchParams();
  });

  // AC BOARD-003: the clear button appears only when a category is active.
  it("renders the clear-category button only when a category filter is active", () => {
    currentSearchParams = new URLSearchParams("category=lore");
    render(<BoardControls availableCategories={CATEGORIES} />);
    expect(screen.getByTestId("clear-category")).toBeInTheDocument();
  });

  it("does not render the clear-category button when no category is active", () => {
    render(<BoardControls availableCategories={CATEGORIES} />);
    expect(screen.queryByTestId("clear-category")).toBeNull();
  });

  // AC BOARD-003: clicking the clear button removes the category param.
  it("removes the category param from the URL when clear-category is clicked", () => {
    currentSearchParams = new URLSearchParams("category=lore&sort=date");
    render(<BoardControls availableCategories={CATEGORIES} />);
    fireEvent.click(screen.getByTestId("clear-category"));

    expect(mockPush).toHaveBeenCalledOnce();
    const pushed = mockPush.mock.calls[0][0];
    const url = new URL(pushed, "http://localhost");
    expect(url.searchParams.has("category")).toBe(false);
    // sort=date must be preserved — only category is cleared
    expect(url.searchParams.get("sort")).toBe("date");
  });
});

describe("BoardControls — rendering (AC BOARD-003)", () => {
  beforeEach(() => {
    currentSearchParams = new URLSearchParams();
  });

  // AC BOARD-003: the component renders both controls with the correct roles.
  it("renders the category and sort selects with accessible labels", () => {
    render(<BoardControls availableCategories={CATEGORIES} />);
    expect(screen.getByLabelText("Catégorie")).toBeInTheDocument();
    expect(screen.getByLabelText("Tri")).toBeInTheDocument();
  });

  // AC BOARD-003: availableCategories are rendered as <option> elements.
  it("renders each availableCategory as an option inside the category select", () => {
    render(<BoardControls availableCategories={CATEGORIES} />);
    for (const cat of CATEGORIES) {
      expect(screen.getByRole("option", { name: cat })).toBeInTheDocument();
    }
  });
});
