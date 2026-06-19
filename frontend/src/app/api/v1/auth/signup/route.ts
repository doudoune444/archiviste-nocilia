/**
 * App Router route handler: POST /api/v1/auth/signup
 *
 * AUTH-002: proxies the signup request to the gateway POST /v1/auth/signup
 * through the bff-proxy module. The gateway returns 201 on success, 409 on
 * email-already-taken, and 400 on invalid credentials.
 *
 * Server-side only: no 'use client'.
 * A09: never logs request body, credentials, or response body.
 */
import type { NextRequest } from "next/server";
import { forward } from "@/lib/bff-proxy";

export const runtime = "nodejs";

export async function POST(request: NextRequest): Promise<Response> {
  return forward(request, "/v1/auth/signup");
}
