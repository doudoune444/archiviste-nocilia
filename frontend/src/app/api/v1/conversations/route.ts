/**
 * App Router route handler: GET /api/v1/conversations
 *
 * Proxies to the gateway GET /v1/conversations through the bff-proxy module.
 * Owner-scoped: the gateway filters by the archiviste_session / archiviste_anon
 * cookie forwarded by bff-proxy — client never supplies identity (A01/IDOR).
 *
 * Server-side only: no 'use client'.
 */
import type { NextRequest } from "next/server";
import { forward } from "@/lib/bff-proxy";

export const runtime = "nodejs";

export async function GET(request: NextRequest): Promise<Response> {
  // AC CHAT-004: bff-proxy is the sole gateway boundary.
  // Owner identity comes from the forwarded cookie — no client-supplied id.
  return forward(request, "/v1/conversations");
}
