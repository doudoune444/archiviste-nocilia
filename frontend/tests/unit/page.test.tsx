// AC: Next.js app boots and layout renders (PLATFORM-001)
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import AccueilPage from "@/app/page";

describe("AccueilPage", () => {
  it("affiche le titre de bienvenue", () => {
    render(<AccueilPage />);
    expect(
      screen.getByRole("heading", { level: 1 })
    ).toHaveTextContent("Bienvenue aux archives de Nocilia");
  });

  it("affiche le lien vers le chat", () => {
    render(<AccueilPage />);
    const link = screen.getByRole("link", { name: /Interroger l'archiviste/i });
    expect(link).toHaveAttribute("href", "/chat");
  });
});
