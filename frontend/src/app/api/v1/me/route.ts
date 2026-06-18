/**
 * App Router route handler: GET /api/v1/me
 *
 * Proxies the request to the Rust gateway GET /v1/me through the bff-proxy
 * module. This is the single server-side entry point — the browser never
 * calls the gateway directly.
 *
 * Server-side only: no 'use client'.
 */
import type { NextRequest } from "next/server";
import { forward } from "@/lib/bff-proxy";

export const runtime = "nodejs";

export async function GET(request: NextRequest): Promise<Response> {
  return forward(request, "/v1/me");
}
