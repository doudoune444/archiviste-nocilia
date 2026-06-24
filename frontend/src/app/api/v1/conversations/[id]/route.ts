/**
 * App Router route handler: DELETE /api/v1/conversations/[id]
 *
 * Proxies to the gateway DELETE /v1/conversations/{id} through bff-proxy.
 * Thin proxy with no business logic of its own: it relays cookies, the
 * request id, the method, and the gateway's status/body (204/404/409)
 * unchanged. Owner-only authorization and the ticket-conflict (409) check
 * are enforced by the gateway (#283); a non-owner passing another user's
 * conversation_id receives an indistinct 404 (A01/IDOR).
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

export async function DELETE(
  request: NextRequest,
  context: RouteContext
): Promise<Response> {
  // id comes from the URL, not from any client-supplied header.
  // Owner-only authorization is enforced by the gateway (A01/IDOR).
  const { id } = await context.params;
  return forward(request, `/v1/conversations/${id}`);
}
