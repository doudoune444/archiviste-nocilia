/**
 * Root page "/" — the chat surface (#247: chat à la racine).
 *
 * Server Component: fetches the initial conversation list server-side via
 * bff-proxy so the sidebar is populated on first render without a client round-trip.
 * Identity comes from the forwarded archiviste_session / archiviste_anon cookie
 * (server is source of truth — no localStorage, no client-supplied identity).
 *
 * On fetch failure the sidebar starts empty (fail-soft: chat is still usable).
 *
 * AC CHAT-004: "stays cleared on reload" — the page never auto-loads a conversation;
 * it only populates the sidebar list. The thread starts empty every time.
 *
 * A01: identity is the cookie forwarded by bff-proxy, never a client-supplied value.
 * A09: conversation content is never logged here.
 */

import { cookies, headers } from "next/headers";
import { forward } from "@/lib/bff-proxy";
import { isConversationList } from "@/components/conversation-history/types";
import type { ConversationSummary } from "@/components/conversation-history/types";
import { ChatShell } from "@/components/conversation-history/ChatShell";

/** Builds a synthetic Request so forward() can extract cookies + request-id. */
async function buildServerRequest(): Promise<Request> {
  const cookieStore = await cookies();
  const headerStore = await headers();

  const outHeaders = new Headers();
  const cookieHeader = cookieStore.toString();
  if (cookieHeader) {
    outHeaders.set("cookie", cookieHeader);
  }
  const requestId = headerStore.get("x-request-id");
  if (requestId !== null) {
    outHeaders.set("x-request-id", requestId);
  }
  return new Request("http://localhost/api/v1/conversations", {
    headers: outHeaders,
  });
}

/** Fetches the owner-scoped conversation list server-side. Returns [] on any failure (fail-soft). */
async function fetchInitialConversations(): Promise<ConversationSummary[]> {
  try {
    const req = await buildServerRequest();
    const res = await forward(req, "/v1/conversations");
    if (!res.ok) return [];
    const body: unknown = await res.json();
    if (!isConversationList(body)) return [];
    return body.conversations;
  } catch {
    // Fail-soft: GATEWAY_URL unset (build/CI), network failure, or timeout.
    // The chat page is fully usable with an empty sidebar.
    return [];
  }
}

export default async function AccueilPage() {
  const initialConversations = await fetchInitialConversations();
  return <ChatShell initialConversations={initialConversations} />;
}
