/**
 * bff-proxy — the single server-side module that calls the Rust gateway
 * on behalf of the current browser request.
 *
 * INVARIANTS (PLATFORM-002):
 * - This is the ONLY place that knows GATEWAY_URL and the cookie names.
 * - Never runs in the browser bundle (no 'use client' allowed here).
 * - A09: never logs cookie values, tokens, or Set-Cookie contents.
 *
 * PLATFORM-004 (AC-2): when CLOUD_RUN_SA_IDENTITY_URL is set (Cloud Run
 * production), bff-proxy fetches a Google-signed ID token from the metadata
 * server and attaches it as Authorization: Bearer <token> on the outbound
 * gateway call.  The gateway IAM binding (gateway_runtime_invoker) requires
 * this token; without it every gateway request returns 403.  On local/CI
 * (no metadata server), the env var is absent and no Authorization header
 * is sent — the gateway must remain accessible without auth in those envs.
 */

// Cookie names are named constants confined to this module.
const SESSION_COOKIE = "archiviste_session";
const ANON_COOKIE = "archiviste_anon";

// A04: every external call must have a hard timeout (security.md).
const GATEWAY_TIMEOUT_MS = 30_000;
const METADATA_TIMEOUT_MS = 5_000;

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
 * Fetches a Google-signed ID token from the Cloud Run metadata server.
 *
 * Only called when CLOUD_RUN_SA_IDENTITY_URL is set — i.e. when running
 * inside Cloud Run where the metadata server is available.  The audience
 * is set to the gateway URL so Cloud Run IAM accepts the token.
 *
 * A09: the returned token string is never logged.
 *
 * Throws on any metadata fetch failure — the caller propagates it so the
 * gateway call is not made without auth in production (no-workaround rule).
 */
async function fetchIdToken(gatewayUrl: string): Promise<string> {
  const metadataBase = process.env["CLOUD_RUN_SA_IDENTITY_URL"];
  if (!metadataBase) {
    // Not running on Cloud Run — no metadata server available.
    return "";
  }

  const audience = encodeURIComponent(gatewayUrl);
  const metadataUrl = `${metadataBase}?audience=${audience}`;

  const metadataRequest = new Request(metadataUrl, {
    headers: new Headers({ "Metadata-Flavor": "Google" }),
  });

  // Propagate failures — do not swallow (A09 / no-workaround).
  const response = await fetch(metadataRequest, {
    signal: AbortSignal.timeout(METADATA_TIMEOUT_MS),
  });

  if (!response.ok) {
    throw new Error(
      `metadata server returned HTTP ${response.status.toString()} for ID token fetch`
    );
  }

  return response.text();
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
 * Forwards archiviste cookies, the request id, and — when an ID token is
 * available — the Authorization: Bearer header for the SA-gated gateway.
 * Deliberately excludes all other incoming headers to avoid header pollution.
 */
function buildOutboundHeaders(
  incoming: Request,
  requestId: string,
  idToken: string
): Headers {
  const headers = new Headers();
  headers.set("x-request-id", requestId);

  const cookie = extractArchivisteCookies(incoming);
  if (cookie !== null) {
    headers.set("cookie", cookie);
  }

  // PLATFORM-004 AC-2: attach ID token for the SA-gated gateway.
  // WHY: gateway IAM binding (gateway_runtime_invoker) restricts invoker to
  // archiviste-runtime SA. Cloud Run validates the Authorization: Bearer JWT
  // against that binding. Without this header every request returns 403.
  // Token is only non-empty when CLOUD_RUN_SA_IDENTITY_URL is set (production).
  // A09: value is not logged.
  //
  // LATENT COUPLING: the ID token is attached on ALL gateway calls forwarded
  // through this function.  For anon-tolerant routes (e.g. /v1/stats, /v1/board)
  // the gateway falls through an invalid or SA-issued JWT → anonymous tier, so
  // the token is harmless.  However, if a route gated by AuthUser or RequireAuthor
  // is ever proxied via forward(), the SA token will cause a spurious 401 because
  // those extractors expect a user-session JWT, not a Cloud Run SA identity token.
  // Resolution when that case arises: add a separate forward variant that omits
  // the Authorization header, or pass the user's session JWT as a bearer token.
  if (idToken.length > 0) {
    headers.set("authorization", `Bearer ${idToken}`);
  }

  return headers;
}

/**
 * Forwards the incoming request to the gateway at `gatewayPath`.
 *
 * - Injects archiviste_session and/or archiviste_anon cookies onto the outbound call.
 * - Propagates (or generates) a request id.
 * - Attaches an ID token (Authorization: Bearer) when running on Cloud Run (PLATFORM-004 AC-2).
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

  // PLATFORM-004 AC-2: fetch ID token before building outbound headers.
  // Throws on failure — caller receives the error (no swallowing).
  const idToken = await fetchIdToken(gatewayUrl);

  const outboundHeaders = buildOutboundHeaders(incoming, requestId, idToken);

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
