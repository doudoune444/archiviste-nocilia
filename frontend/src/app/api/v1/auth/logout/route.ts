/**
 * App Router route handler: POST /api/v1/auth/logout
 *
 * AUTH-001: proxies the logout request to the gateway POST /v1/auth/logout
 * through the bff-proxy module. The gateway revokes the server-side session
 * and emits Set-Cookie with Max-Age=0 to clear the browser cookie.
 * bff-proxy relays that Set-Cookie to the browser unchanged.
 *
 * The gateway requires a valid Authorization: Bearer <jwt> header; the
 * browser must send the archiviste_session cookie which the gateway reads
 * to authenticate the caller (the JWT is stored in the session cookie).
 *
 * Server-side only: no 'use client'.
 * A09: never logs cookies, tokens, or Set-Cookie values.
 */
import type { NextRequest } from "next/server";
import { forward } from "@/lib/bff-proxy";

export const runtime = "nodejs";

export async function POST(request: NextRequest): Promise<Response> {
  return forward(request, "/v1/auth/logout");
}
