// Issue #287 — full delete flow driven by ChatShell:
//   trash click → ConfirmDialog (named) → DELETE → reconcile.
//
// Tested through the rendered DOM + stubbed fetch (vi.stubGlobal), never internal
// state. The history list (trash buttons) is injected into the sidebar, so we
// render SidebarShell alongside ChatShell (prior art: chat-shell-b1.test.tsx).
//
// Acceptance criteria covered here:
//   - trash click opens the ConfirmDialog naming the target conversation
//   - Annuler / Esc / overlay close the modal with NO network call
//   - Confirm issues DELETE /api/v1/conversations/{id} and greys the item
//   - 204 removes the item only after the server confirms (list reconcile)
//   - 409 lifts the grey and shows the "signalement en cours" message
//   - a non-409 error lifts the grey and shows an inline error
//   - deleting the open conversation resets the thread; deleting another does not

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
  within,
} from "@testing-library/react";
import React from "react";
import { ChatShell } from "@/components/conversation-history/ChatShell";
import { SidebarChatProvider } from "@/components/app-sidebar/SidebarChatContext";
import { SidebarShell } from "@/components/app-sidebar/SidebarShell";
import type { ConversationSummary } from "@/components/conversation-history/types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/",
}));
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
  }: {
    href: string;
    children: React.ReactNode;
  }) => <a href={href}>{children}</a>,
}));

vi.mock("@/components/chat/chat.module.css", () => ({
  default: new Proxy({}, { get: (_t, p: string) => p }),
}));
vi.mock(
  "@/components/conversation-history/ConversationHistory.module.css",
  () => ({ default: new Proxy({}, { get: (_t, p: string) => p }) })
);
vi.mock("@/components/conversation-history/ChatShell.module.css", () => ({
  default: new Proxy({}, { get: (_t, p: string) => p }),
}));
vi.mock("@/components/confirm-dialog/ConfirmDialog.module.css", () => ({
  default: new Proxy({}, { get: (_t, p: string) => p }),
}));

const TRASH_LABEL = "Supprimer la conversation";

function makeConversation(
  overrides: Partial<ConversationSummary> = {}
): ConversationSummary {
  return {
    id: "c1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T10:30:00Z",
    message_count: 4,
    title: "Quelle est la capitale ?",
    has_ticket: false,
    ...overrides,
  };
}

function listResponse(conversations: ConversationSummary[]): Response {
  return new Response(JSON.stringify({ conversations }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function renderShell(initialConversations: ConversationSummary[]) {
  return render(
    <SidebarChatProvider>
      <SidebarShell identity={{ tier: "anonymous", email: null }} />
      <ChatShell initialConversations={initialConversations} />
    </SidebarChatProvider>
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ChatShell delete — confirmation modal (#287)", () => {
  it("opens a ConfirmDialog naming the target conversation when the trash is clicked", () => {
    renderShell([makeConversation({ id: "c1", title: "Recette de tarte" })]);

    fireEvent.click(screen.getByRole("button", { name: TRASH_LABEL }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Supprimer cette conversation ?");
    expect(dialog).toHaveTextContent(/Recette de tarte/);
    expect(dialog).toHaveTextContent(/définitive/i);
    expect(within(dialog).getByRole("button", { name: "Supprimer" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Annuler" })).toBeInTheDocument();
  });
});

describe("ChatShell delete — cancel paths make no network call (#287)", () => {
  it("closes without a DELETE when Annuler is clicked", () => {
    const mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);
    renderShell([makeConversation({ id: "c1" })]);

    fireEvent.click(screen.getByRole("button", { name: TRASH_LABEL }));
    fireEvent.click(screen.getByRole("button", { name: "Annuler" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("closes without a DELETE when Escape is pressed", () => {
    const mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);
    renderShell([makeConversation({ id: "c1" })]);

    fireEvent.click(screen.getByRole("button", { name: TRASH_LABEL }));
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

describe("ChatShell delete — confirm issues DELETE and reconciles (#287)", () => {
  it("calls DELETE /api/v1/conversations/{id} and removes the item only after 204", async () => {
    const mockFetch = vi
      .fn()
      // 1) the DELETE call
      .mockImplementationOnce(() =>
        Promise.resolve(new Response(null, { status: 204 }))
      )
      // 2) the list reconcile (refreshConversations) — now empty
      .mockImplementationOnce(() => Promise.resolve(listResponse([])));
    vi.stubGlobal("fetch", mockFetch);

    renderShell([makeConversation({ id: "c1", title: "À supprimer" })]);

    fireEvent.click(screen.getByRole("button", { name: TRASH_LABEL }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Supprimer" }));
    });

    expect(mockFetch).toHaveBeenNthCalledWith(
      1,
      "/api/v1/conversations/c1",
      expect.objectContaining({ method: "DELETE" })
    );
    await waitFor(() => {
      expect(screen.queryByText("À supprimer")).not.toBeInTheDocument();
    });
  });

  it("greys the item with a busy spinner while the DELETE is in flight", async () => {
    let resolveDelete: (r: Response) => void = () => {};
    const deletePromise = new Promise<Response>((resolve) => {
      resolveDelete = resolve;
    });
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() => deletePromise)
      .mockImplementationOnce(() => Promise.resolve(listResponse([])));
    vi.stubGlobal("fetch", mockFetch);

    renderShell([makeConversation({ id: "c1" })]);

    fireEvent.click(screen.getByRole("button", { name: TRASH_LABEL }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Supprimer" }));
    });

    const item = screen.getByTestId("conversation-item-c1");
    expect(item).toHaveAttribute("aria-busy", "true");
    expect(item).toBeDisabled();

    await act(async () => {
      resolveDelete(new Response(null, { status: 204 }));
    });
  });
});

describe("ChatShell delete — failure surfaces (#287)", () => {
  it("lifts the grey and shows the signalement message on 409", async () => {
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() =>
        Promise.resolve(new Response(null, { status: 409 }))
      );
    vi.stubGlobal("fetch", mockFetch);

    renderShell([makeConversation({ id: "c1", title: "Signalée" })]);

    fireEvent.click(screen.getByRole("button", { name: TRASH_LABEL }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Supprimer" }));
    });

    await waitFor(() => {
      expect(screen.getByText(/signalement est en cours/i)).toBeInTheDocument();
    });
    // item survives (never optimistic), grey lifted.
    expect(screen.getByText("Signalée")).toBeInTheDocument();
    expect(screen.getByTestId("conversation-item-c1")).not.toHaveAttribute(
      "aria-busy",
      "true"
    );
    // exactly one network call: the failed DELETE, no reconcile.
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it("lifts the grey and shows an inline error on a generic failure", async () => {
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() =>
        Promise.resolve(new Response(null, { status: 500 }))
      );
    vi.stubGlobal("fetch", mockFetch);

    renderShell([makeConversation({ id: "c1", title: "Boom" })]);

    fireEvent.click(screen.getByRole("button", { name: TRASH_LABEL }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Supprimer" }));
    });

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByText("Boom")).toBeInTheDocument();
    expect(screen.getByTestId("conversation-item-c1")).not.toHaveAttribute(
      "aria-busy",
      "true"
    );
  });
});

describe("ChatShell delete — conditional thread reset (#287)", () => {
  // Selection is observable via aria-current on the history item (set
  // synchronously on select). After deleting the open conversation the thread
  // returns to "Nouvelle conversation" → no item carries aria-current.
  it("clears the open selection when the open conversation is deleted", async () => {
    const mockFetch = vi
      .fn()
      // selecting the conversation loads its transcript
      .mockImplementationOnce(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({ conversation_id: "c1", messages: [] }),
            { status: 200, headers: { "content-type": "application/json" } }
          )
        )
      )
      // DELETE
      .mockImplementationOnce(() =>
        Promise.resolve(new Response(null, { status: 204 }))
      )
      // reconcile
      .mockImplementationOnce(() => Promise.resolve(listResponse([])));
    vi.stubGlobal("fetch", mockFetch);

    renderShell([makeConversation({ id: "c1", title: "Ouverte" })]);

    await act(async () => {
      fireEvent.click(screen.getByTestId("conversation-item-c1"));
    });
    expect(screen.getByTestId("conversation-item-c1")).toHaveAttribute(
      "aria-current",
      "true"
    );

    fireEvent.click(screen.getByRole("button", { name: TRASH_LABEL }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Supprimer" }));
    });

    // open item removed, thread reset, empty composer present.
    await waitFor(() => {
      expect(screen.queryByText("Ouverte")).not.toBeInTheDocument();
    });
    expect(
      screen.getByRole("textbox", { name: /votre question/i })
    ).toBeInTheDocument();
  });

  it("keeps the open selection when a different conversation is deleted", async () => {
    const mockFetch = vi
      .fn()
      .mockImplementationOnce(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({ conversation_id: "open", messages: [] }),
            { status: 200, headers: { "content-type": "application/json" } }
          )
        )
      )
      .mockImplementationOnce(() =>
        Promise.resolve(new Response(null, { status: 204 }))
      )
      .mockImplementationOnce(() =>
        Promise.resolve(
          listResponse([makeConversation({ id: "open", title: "Ouverte" })])
        )
      );
    vi.stubGlobal("fetch", mockFetch);

    renderShell([
      makeConversation({ id: "open", title: "Ouverte" }),
      makeConversation({ id: "other", title: "Autre" }),
    ]);

    await act(async () => {
      fireEvent.click(screen.getByTestId("conversation-item-open"));
    });
    expect(screen.getByTestId("conversation-item-open")).toHaveAttribute(
      "aria-current",
      "true"
    );

    const otherTrash = within(
      screen.getByTestId("conversation-item-other").closest("div") as HTMLElement
    ).getByRole("button", { name: TRASH_LABEL });
    fireEvent.click(otherTrash);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Supprimer" }));
    });

    // other removed, open selection preserved.
    await waitFor(() => {
      expect(screen.queryByText("Autre")).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("conversation-item-open")).toHaveAttribute(
      "aria-current",
      "true"
    );
  });
});
