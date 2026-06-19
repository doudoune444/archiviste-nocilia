/**
 * App Router route handler: GET /api/v1/status
 *
 * Proxies to the gateway GET /v1/status through the bff-proxy module.
 * Client Components cannot import bff-proxy (server-only module), so the
 * DepHealth island fetches this same-origin route instead.
 *
 * AC3 (WEBOBS-002): polling goes through the bff-proxy, not directly to
 * the gateway. No new backend endpoint — uses the existing public /v1/status.
 *
 * Server-side only: no 'use client'.
 */
import type { NextRequest } from "next/server";
import { forward } from "@/lib/bff-proxy";

export const runtime = "nodejs";

export async function GET(request: NextRequest): Promise<Response> {
  return forward(request, "/v1/status");
}
