/**
 * App Router route handler: POST /api/v1/report-contradiction (CHAT-005).
 *
 * Proxies to the gateway POST /v1/report-contradiction through the bff-proxy
 * module. The gateway resolves request_id from its own middleware — NOT from
 * the client body. The client body must carry: claim, conversation_id,
 * and optionally citations and force.
 *
 * bff-proxy.forward() forwards cookies (owner identity), Content-Type, and
 * x-request-id header. The gateway ignores any request_id in the JSON body.
 *
 * A09: the claim text (may contain PII) is never logged here.
 * A01: ownership check is enforced server-side by the gateway via forwarded cookie.
 * Server-side only: no 'use client'.
 */
import type { NextRequest } from "next/server";
import { forward } from "@/lib/bff-proxy";

export const runtime = "nodejs";

export async function POST(request: NextRequest): Promise<Response> {
  // AC CHAT-005: bff-proxy is the sole gateway boundary.
  // Cookies (archiviste_session / archiviste_anon) are forwarded verbatim
  // so the gateway IDOR check can identify the caller.
  return forward(request, "/v1/report-contradiction");
}
