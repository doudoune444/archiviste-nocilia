/**
 * App Router route handler: POST /api/v1/chat/stream (CHAT-002).
 *
 * Relays the POST to the gateway POST /v1/chat/stream through the bff-proxy
 * module and streams the SSE body back to the browser verbatim.
 *
 * bff-proxy.forward() returns new Response(gatewayResponse.body, ...) so the
 * ReadableStream<Uint8Array> flows directly to the browser without buffering.
 *
 * RISK NOTE (no-workaround.md): bff-proxy.forward() attaches
 * AbortSignal.timeout(GATEWAY_TIMEOUT_MS = 30_000) on its internal fetch()
 * call. For long LLM responses that exceed 30 s this signal WILL abort the
 * stream mid-flight, truncating the SSE response. This module deliberately
 * does NOT patch bff-proxy (PLATFORM-002 owned). The risk is documented in
 * docs/blockers.md — see entry 2026-06-19 CHAT-002. Resolution: the architect
 * or a dedicated bff-proxy ticket must increase GATEWAY_TIMEOUT_MS or switch
 * to a streaming-aware fetch path before this becomes a production problem.
 *
 * Server-side only: no 'use client'.
 */
import type { NextRequest } from "next/server";
import { forward } from "@/lib/bff-proxy";

export const runtime = "nodejs";

export async function POST(request: NextRequest): Promise<Response> {
  // AC: bff-proxy is the sole gateway boundary (PLATFORM-002 invariant).
  return forward(request, "/v1/chat/stream");
}
