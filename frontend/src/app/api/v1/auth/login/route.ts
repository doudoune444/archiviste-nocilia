/**
 * App Router route handler: POST /api/v1/auth/login
 *
 * AUTH-001: proxies the login request to the gateway POST /v1/auth/login
 * through the bff-proxy module. The gateway sets archiviste_session via
 * Set-Cookie which bff-proxy relays to the browser unchanged.
 *
 * Server-side only: no 'use client'.
 * A09: never logs request body, credentials, or Set-Cookie values.
 */
import type { NextRequest } from "next/server";
import { forward } from "@/lib/bff-proxy";

export const runtime = "nodejs";

export async function POST(request: NextRequest): Promise<Response> {
  return forward(request, "/v1/auth/login");
}
