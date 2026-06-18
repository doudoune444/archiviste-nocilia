/**
 * App Router route handler: GET /api/v1/board
 *
 * Proxies to the gateway GET /v1/board through the bff-proxy module.
 * The caller's query string (sort, limit, offset, category) is forwarded
 * verbatim so the LoadMoreButton client component can paginate.
 *
 * Server-side only: no 'use client'.
 */
import type { NextRequest } from "next/server";
import { forward } from "@/lib/bff-proxy";

export const runtime = "nodejs";

export async function GET(request: NextRequest): Promise<Response> {
  // AC1: bff-proxy is the sole gateway boundary. Query params are forwarded
  // inside gatewayPath because forward() does not append them itself.
  const search = request.nextUrl.search;
  return forward(request, `/v1/board${search}`);
}
