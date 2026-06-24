/**
 * App Router route handler: DELETE /api/v1/conversations/[id]
 *
 * Thin proxy to the gateway DELETE /v1/conversations/{id} through bff-proxy.
 * No business logic of its own: it relays cookies, headers, method, and the
 * gateway's status (204/404/409) and body unchanged.
 *
 * IDOR (A01): the gateway authorizes ownership of the conversation — the id
 * comes from the URL, never from a client-supplied header, and identity comes
 * from the forwarded archiviste_session / archiviste_anon cookie.
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
  const { id } = await context.params;
  return forward(request, `/v1/conversations/${id}`);
}
