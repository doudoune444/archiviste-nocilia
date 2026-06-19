/**
 * App Router route handler: GET /api/v1/tickets
 *
 * Proxies to the gateway GET /v1/tickets through the bff-proxy module.
 * The caller's query string (limit, offset) is forwarded verbatim.
 * The gateway enforces author-tier access — a 401 or 403 is relayed as-is.
 *
 * Server-side only: no 'use client'.
 */
import type { NextRequest } from "next/server";
import { forward } from "@/lib/bff-proxy";

export const runtime = "nodejs";

export async function GET(request: NextRequest): Promise<Response> {
  // AC DASH-001: bff-proxy is the sole gateway boundary. Query params forwarded
  // verbatim so the dashboard RSC can pass limit/offset for pagination.
  const search = request.nextUrl.search;
  return forward(request, `/v1/tickets${search}`);
}
