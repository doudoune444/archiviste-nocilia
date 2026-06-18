/**
 * bff-proxy — the single server-side module that calls the Rust gateway
 * on behalf of the current browser request.
 *
 * INVARIANTS (PLATFORM-002):
 * - This is the ONLY place that knows GATEWAY_URL and the cookie names.
 * - Never runs in the browser bundle (no 'use client' allowed here).
 * - A09: never logs cookie values, tokens, or Set-Cookie contents.
 */

// Cookie names are named constants confined to this module.
const SESSION_COOKIE = "archiviste_session";
const ANON_COOKIE = "archiviste_anon";

// A04: every external call must have a hard timeout (security.md).
const GATEWAY_TIMEOUT_MS = 30_000;

/**
 * Reads GATEWAY_URL from the environment.
 * Throws at call-time (not module load) so Next.js can tree-shake safely.
 * No hardcoded fallback — secret-hygiene forbids getenv("X","default").
 */
function resolveGatewayUrl(): string {
  const url = process.env["GATEWAY_URL"];
  if (!url) {
    throw new Error("GATEWAY_URL environment variable is not set");
  }
  return url;
}

/**
 * Extracts only the archiviste cookies from the incoming Cookie header and
 * returns a Cookie string ready for the outbound request, or null if neither
 * cookie is present.
 */
function extractArchivisteCookies(incoming: Request): string | null {
  const rawCookie = incoming.headers.get("cookie");
  if (!rawCookie) return null;

  const pairs = rawCookie.split(";").map((p) => p.trim());
  const kept = pairs.filter((p) => {
    const name = p.split("=")[0]?.trim() ?? "";
    return name === SESSION_COOKIE || name === ANON_COOKIE;
  });

  return kept.length > 0 ? kept.join("; ") : null;
}

/**
 * Resolves the request-id: reuse the incoming x-request-id header if present,
 * otherwise generate a new UUID v4 via Node crypto.
 */
function resolveRequestId(incoming: Request): string {
  return incoming.headers.get("x-request-id") ?? crypto.randomUUID();
}

/**
 * Builds the outbound headers for the gateway call.
 * Forwards archiviste cookies and the request id.
 * Deliberately excludes all other incoming headers to avoid header pollution.
 */
function buildOutboundHeaders(
  incoming: Request,
  requestId: string
): Headers {
  const headers = new Headers();
  headers.set("x-request-id", requestId);

  const cookie = extractArchivisteCookies(incoming);
  if (cookie !== null) {
    headers.set("cookie", cookie);
  }

  return headers;
}

/**
 * Forwards the incoming request to the gateway at `gatewayPath`.
 *
 * - Injects archiviste_session and/or archiviste_anon cookies onto the outbound call.
 * - Propagates (or generates) a request id.
 * - Relays any Set-Cookie header from the gateway to the browser unchanged.
 * - This is the only caller of fetch() for gateway calls.
 *
 * A09: only route + status + request-id may be logged. Cookie values are never logged.
 */
export async function forward(
  incoming: Request,
  gatewayPath: string
): Promise<Response> {
  const gatewayUrl = resolveGatewayUrl();
  const requestId = resolveRequestId(incoming);
  const outboundHeaders = buildOutboundHeaders(incoming, requestId);

  const outboundRequest = new Request(`${gatewayUrl}${gatewayPath}`, {
    method: incoming.method,
    headers: outboundHeaders,
    // body is not forwarded for GET — extend when needed for POST/PUT/DELETE.
  });

  const gatewayResponse = await fetch(outboundRequest, {
    signal: AbortSignal.timeout(GATEWAY_TIMEOUT_MS),
  });

  // Build the response relayed back to the browser.
  const responseHeaders = new Headers();
  responseHeaders.set("x-request-id", requestId);

  // AC2: relay Set-Cookie unchanged so the browser receives the session/anon token.
  // getSetCookie() returns each Set-Cookie header as a separate string — avoids
  // the comma-join corruption that Headers.get("set-cookie") produces when the
  // gateway sends multiple Set-Cookie headers (e.g. session + anon on login).
  // A09: we copy the header values but do NOT log them.
  for (const cookie of gatewayResponse.headers.getSetCookie()) {
    responseHeaders.append("set-cookie", cookie);
  }

  const contentType = gatewayResponse.headers.get("content-type");
  if (contentType !== null) {
    responseHeaders.set("content-type", contentType);
  }

  return new Response(gatewayResponse.body, {
    status: gatewayResponse.status,
    headers: responseHeaders,
  });
}
