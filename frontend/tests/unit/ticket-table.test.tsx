// BOARD-002 AC3+AC4 — TicketTable render tests.
// Playwright smoke is substituted by RTL render tests (Playwright is already
// wired in the repo but requires a live Next.js server; the table is a pure
// presentational component that can be tested in isolation with jsdom).
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { TicketTable } from "@/components/board/TicketTable";
import type { BoardTicket } from "@/components/board/types";

function makeTicket(overrides: Partial<BoardTicket> = {}): BoardTicket {
  return {
    id: "00000000-0000-4000-8000-000000000001",
    conversation_id: "00000000-0000-4000-8000-000000000002",
    question: "Qui est la gardienne du temple ?",
    category: "personnages",
    priority_score: 3,
    status: "open",
    created_at: "2026-01-15T10:00:00Z",
    updated_at: "2026-01-15T10:00:00Z",
    judges_not_passed: false,
    ...overrides,
  };
}

describe("TicketTable (AC3, AC4)", () => {
  // AC4: empty-board state spans ALL columns
  it("renders empty state with colSpan covering all columns when tickets is empty", () => {
    render(<TicketTable tickets={[]} />);
    const emptyCell = screen.getByTestId("empty-board");
    expect(emptyCell).toBeInTheDocument();
    expect(emptyCell).toHaveAttribute("colspan", "5");
    expect(emptyCell.tagName).toBe("TD");
  });

  // AC4: empty state text is present
  it("shows a descriptive message when no tickets", () => {
    render(<TicketTable tickets={[]} />);
    expect(screen.getByTestId("empty-board")).toHaveTextContent(
      "Aucun ticket ouvert"
    );
  });

  // AC3: rows render with question as safe text (not HTML)
  it("renders the question as text — no raw HTML injection", () => {
    const xssAttempt = '<img src=x onerror="alert(1)">';
    render(<TicketTable tickets={[makeTicket({ question: xssAttempt })]} />);
    // The injected string must appear verbatim as text, not be parsed as HTML.
    expect(screen.getByText(xssAttempt)).toBeInTheDocument();
    // There must be no img element injected.
    expect(document.querySelector("img")).toBeNull();
  });

  // AC3: category rendered as chip text (safe text only)
  it("renders category as a chip with text content only", () => {
    render(<TicketTable tickets={[makeTicket({ category: "lieux" })]} />);
    expect(screen.getByText("lieux")).toBeInTheDocument();
  });

  // AC3: priority_score displayed with visual intensity data attribute
  it("shows priority score and sets data-level=high when score >= 5", () => {
    render(<TicketTable tickets={[makeTicket({ priority_score: 7 })]} />);
    const priorityEl = screen.getByLabelText("priorité 7");
    expect(priorityEl).toHaveAttribute("data-level", "high");
    expect(priorityEl).toHaveTextContent("7");
  });

  it("sets data-level=medium when priority_score is 2-4", () => {
    render(<TicketTable tickets={[makeTicket({ priority_score: 3 })]} />);
    expect(screen.getByLabelText("priorité 3")).toHaveAttribute(
      "data-level",
      "medium"
    );
  });

  it("sets data-level=low when priority_score is 0-1", () => {
    render(<TicketTable tickets={[makeTicket({ priority_score: 1 })]} />);
    expect(screen.getByLabelText("priorité 1")).toHaveAttribute(
      "data-level",
      "low"
    );
  });

  // AC3: badge shown when judges_not_passed true
  it("renders the not-confirmed badge when judges_not_passed is true", () => {
    render(<TicketTable tickets={[makeTicket({ judges_not_passed: true })]} />);
    expect(screen.getByTestId("badge-not-confirmed")).toBeInTheDocument();
  });

  // AC3: no badge when judges_not_passed false
  it("renders no badge when judges_not_passed is false", () => {
    render(<TicketTable tickets={[makeTicket({ judges_not_passed: false })]} />);
    expect(screen.queryByTestId("badge-not-confirmed")).toBeNull();
  });

  // AC3: multiple rows render correctly
  it("renders one row per ticket", () => {
    const tickets = [
      makeTicket({ id: "id-1", question: "Question A" }),
      makeTicket({ id: "id-2", question: "Question B" }),
    ];
    render(<TicketTable tickets={tickets} />);
    expect(screen.getByText("Question A")).toBeInTheDocument();
    expect(screen.getByText("Question B")).toBeInTheDocument();
    // Table must have 5 columns in the header
    const headers = screen.getAllByRole("columnheader");
    expect(headers).toHaveLength(5);
  });

  // AC3: date column uses <time> element with datetime attribute
  it("renders date in a <time> element with the ISO dateTime attribute", () => {
    render(
      <TicketTable
        tickets={[makeTicket({ created_at: "2026-01-15T10:00:00Z" })]}
      />
    );
    const timeEl = screen.getByRole("time", { hidden: true });
    expect(timeEl).toHaveAttribute("datetime", "2026-01-15T10:00:00Z");
  });

  // Security: table uses accessible label
  it("table has an accessible aria-label", () => {
    render(<TicketTable tickets={[]} />);
    expect(
      screen.getByRole("table", { name: "tickets lore-gap" })
    ).toBeInTheDocument();
  });
});
