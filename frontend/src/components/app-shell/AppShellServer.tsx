/**
 * AppShellServer — server component that feeds the client AppShell (#245).
 *
 * Fetches identity (GET /v1/me) and the owner-scoped conversation list
 * (GET /v1/conversations) server-side through the bff-proxy, then renders the
 * client AppShell with those props. Both fetches fail soft: any error degrades
 * to the anonymous tier / empty history so the layout never crashes.
 *
 * A01: identity is the cookie forwarded by bff-proxy, never a client value.
 * A09: email is rendered as text only; conversation content is never logged.
 */

import { cookies, headers } from "next/headers";
import { forward } from "@/lib/bff-proxy";
import { isConversationList } from "@/components/conversation-history/types";
import type { ConversationSummary } from "@/components/conversation-history/types";
import { AppShell } from "./AppShell";
import type { Tier } from "./SidebarNav";

interface Identity {
  tier: Tier;
  email: string | null;
}

const VALID_TIERS: ReadonlySet<string> = new Set([
  "anonymous",
  "member",
  "author",
]);

function isIdentity(value: unknown): value is Identity {
  if (typeof value !== "object" || value === null) return false;
  const obj = value as Record<string, unknown>;
  if (!VALID_TIERS.has(obj["tier"] as string)) return false;
  if (obj["email"] !== null && typeof obj["email"] !== "string") return false;
  return true;
}

/** Builds a synthetic server-side Request carrying the browser cookies + request-id. */
async function buildServerRequest(path: string): Promise<Request> {
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
  return new Request(`http://server${path}`, { headers: outHeaders });
}

async function fetchIdentity(): Promise<Identity> {
  const anonymous: Identity = { tier: "anonymous", email: null };
  try {
    const req = await buildServerRequest("/api/v1/me");
    const res = await forward(req, "/v1/me");
    if (!res.ok) return anonymous;
    const body: unknown = await res.json();
    return isIdentity(body) ? body : anonymous;
  } catch {
    return anonymous;
  }
}

async function fetchConversations(): Promise<ConversationSummary[]> {
  try {
    const req = await buildServerRequest("/api/v1/conversations");
    const res = await forward(req, "/v1/conversations");
    if (!res.ok) return [];
    const body: unknown = await res.json();
    if (!isConversationList(body)) return [];
    return body.conversations;
  } catch {
    return [];
  }
}

interface AppShellServerProps {
  children: React.ReactNode;
}

export async function AppShellServer({ children }: AppShellServerProps) {
  const [identity, conversations] = await Promise.all([
    fetchIdentity(),
    fetchConversations(),
  ]);

  return (
    <AppShell
      tier={identity.tier}
      email={identity.email}
      initialConversations={conversations}
    >
      {children}
    </AppShell>
  );
}
