/**
 * App Router route handler: DELETE /api/v1/conversations/[id]
 *
 * Thin proxy to the gateway DELETE /v1/conversations/{id} through bff-proxy.
 * No business logic of its own: forward(...) relays method, archiviste cookies,
 * request_id, and the gateway's status/body (204/404/409) unchanged.
 *
 * Owner-only deletion (PRD #282): the gateway filters by user_id in SQL and
 * returns an indistinct 404 for a conversation that is missing or owned by
 * someone else, so a non-owner passing another user's id learns nothing
 * (A01/IDOR). The id comes from the URL, never from a client-supplied header.
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
