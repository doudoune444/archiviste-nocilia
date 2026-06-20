/**
 * App Router route handler: GET /api/v1/conversations/[id]/messages
 *
 * Proxies to the gateway GET /v1/conversations/{id}/messages through bff-proxy.
 * Owner-scoped: the gateway enforces ownership via SQL JOIN on user_id so a caller
 * passing another user's conversation_id learns nothing (A01/IDOR).
 *
 * Next.js 15: route context params is a Promise — must be awaited.
 *
 * Server-side only: no 'use client'.
 */
import type { NextRequest } from "next/server";
import { forward } from "@/lib/bff-proxy";

export const runtime = "nodejs";

interface RouteContext {
  params: Promise<{ id: string }>;
}

export async function GET(
  request: NextRequest,
  context: RouteContext
): Promise<Response> {
  // AC CHAT-004: id comes from the URL, not from any client-supplied header.
  // The gateway JOIN ensures only the owner can read their messages (A01/IDOR).
  const { id } = await context.params;
  return forward(request, `/v1/conversations/${id}/messages`);
}
