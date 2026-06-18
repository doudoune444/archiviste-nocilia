// AC: PLATFORM-002 — bff-proxy deep module
// AC1: Incoming request with archiviste_session and/or archiviste_anon → outbound call carries same cookies + request id
// AC2: A Set-Cookie from the gateway is relayed back to the browser unchanged
// AC3: Gateway URL and cookie names live ONLY in this module
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { forward } from "@/lib/bff-proxy";

// The gateway URL must be read from env — provide a test value.
const TEST_GATEWAY_URL = "http://gateway.test:8080";

function makeIncomingRequest(
  cookieHeader: string | null,
  requestId?: string
): Request {
  const headers = new Headers();
  if (cookieHeader !== null) {
    headers.set("cookie", cookieHeader);
  }
  if (requestId !== undefined) {
    headers.set("x-request-id", requestId);
  }
  return new Request("http://next.test/api/v1/me", { headers });
}

describe("forward()", () => {
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    // Inject GATEWAY_URL into env for each test.
    process.env["GATEWAY_URL"] = TEST_GATEWAY_URL;

    // Mock the global fetch used by forward().
    fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env["GATEWAY_URL"];
  });

  // AC1 — both cookies present
  it("forwards both session and anon cookies to the gateway", async () => {
    fetchSpy.mockResolvedValue(
      new Response(null, { status: 200 })
    );

    const incoming = makeIncomingRequest(
      "archiviste_session=sess123; archiviste_anon=anon456; _other=x"
    );
    await forward(incoming, "/v1/me");

    expect(fetchSpy).toHaveBeenCalledOnce();
    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    const outboundCookie = outboundReq.headers.get("cookie");

    // Must carry both archiviste cookies
    expect(outboundCookie).toContain("archiviste_session=sess123");
    expect(outboundCookie).toContain("archiviste_anon=anon456");

    // x-request-id must be present
    expect(outboundReq.headers.get("x-request-id")).toBeTruthy();
  });

  // AC1 — only session cookie present
  it("forwards only archiviste_session when anon absent", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 200 }));

    const incoming = makeIncomingRequest("archiviste_session=sess999");
    await forward(incoming, "/v1/me");

    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    const outboundCookie = outboundReq.headers.get("cookie");

    expect(outboundCookie).toContain("archiviste_session=sess999");
    expect(outboundCookie).not.toContain("archiviste_anon");
  });

  // AC1 — only anon cookie present
  it("forwards only archiviste_anon when session absent", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 200 }));

    const incoming = makeIncomingRequest("archiviste_anon=anon777");
    await forward(incoming, "/v1/me");

    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    const outboundCookie = outboundReq.headers.get("cookie");

    expect(outboundCookie).toContain("archiviste_anon=anon777");
    expect(outboundCookie).not.toContain("archiviste_session");
  });

  // AC1 — neither cookie present → no cookie header sent
  it("sends no cookie header when neither archiviste cookie is present", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 200 }));

    const incoming = makeIncomingRequest("_ga=GA1.1; other=val");
    await forward(incoming, "/v1/me");

    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    expect(outboundReq.headers.get("cookie")).toBeNull();
  });

  // AC1 — no cookie header at all → no cookie header sent
  it("sends no cookie header when incoming request has no cookies", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 200 }));

    const incoming = makeIncomingRequest(null);
    await forward(incoming, "/v1/me");

    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    expect(outboundReq.headers.get("cookie")).toBeNull();
  });

  // AC1 — request-id reused from incoming header
  it("reuses x-request-id from the incoming request if present", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 200 }));

    const incoming = makeIncomingRequest(null, "req-abc-123");
    await forward(incoming, "/v1/me");

    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    expect(outboundReq.headers.get("x-request-id")).toBe("req-abc-123");
  });

  // AC1 — request-id generated when not present
  it("generates a new x-request-id when the incoming request has none", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 200 }));

    const incoming = makeIncomingRequest(null);
    await forward(incoming, "/v1/me");

    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    const generatedId = outboundReq.headers.get("x-request-id");
    // UUID v4 pattern
    expect(generatedId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
    );
  });

  // AC2 — Set-Cookie from gateway is relayed unchanged
  it("relays gateway Set-Cookie header back to the browser", async () => {
    const setCookieValue =
      "archiviste_session=newsess; Path=/; HttpOnly; SameSite=Lax";
    fetchSpy.mockResolvedValue(
      new Response(null, {
        status: 200,
        headers: { "set-cookie": setCookieValue },
      })
    );

    const incoming = makeIncomingRequest(null);
    const response = await forward(incoming, "/v1/me");

    expect(response.headers.get("set-cookie")).toBe(setCookieValue);
  });

  // AC2 — no Set-Cookie when gateway sends none
  it("does not add Set-Cookie when gateway sends none", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 200 }));

    const incoming = makeIncomingRequest(null);
    const response = await forward(incoming, "/v1/me");

    expect(response.headers.get("set-cookie")).toBeNull();
  });

  // AC2 — TWO Set-Cookie headers from gateway must be relayed as two distinct
  // headers, not comma-joined. Set-Cookie values legitimately contain commas
  // (Expires=Wed, 09 Jun 2027 ...) so a comma-joined string corrupts both cookies.
  it("relays two Set-Cookie headers from gateway as separate cookies, not comma-joined", async () => {
    // Build a gateway response whose Headers object carries two distinct Set-Cookie
    // entries. We append both to a real Headers instance so getSetCookie() returns
    // an array of length 2.
    const gatewayHeaders = new Headers();
    gatewayHeaders.append(
      "set-cookie",
      "archiviste_session=newsess; Expires=Wed, 09 Jun 2027 10:18:14 GMT; Path=/; HttpOnly; SameSite=Lax"
    );
    gatewayHeaders.append(
      "set-cookie",
      "archiviste_anon=; Path=/; Max-Age=0"
    );

    fetchSpy.mockResolvedValue(
      new Response(null, { status: 200, headers: gatewayHeaders })
    );

    const incoming = makeIncomingRequest(null);
    const response = await forward(incoming, "/v1/me");

    // getSetCookie() on the relayed Response must return both cookies intact.
    const relayed = response.headers.getSetCookie();
    expect(relayed).toHaveLength(2);
    expect(relayed[0]).toBe(
      "archiviste_session=newsess; Expires=Wed, 09 Jun 2027 10:18:14 GMT; Path=/; HttpOnly; SameSite=Lax"
    );
    expect(relayed[1]).toBe("archiviste_anon=; Path=/; Max-Age=0");

    // The Expires field of the first cookie contains a comma. If the old buggy
    // .set(joined-string) path were used, both cookies would be merged into one
    // string that contains that exact comma — the second cookie would vanish as
    // a parse artefact. Verify the second cookie is still its own distinct entry.
    expect(relayed[1]).toBe("archiviste_anon=; Path=/; Max-Age=0");
  });

  // A04: outbound fetch must carry a timeout signal so a hung gateway does not
  // block the server indefinitely (security.md A04 — hard cap on external calls).
  it("calls fetch with an AbortSignal so the gateway call has a timeout", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 200 }));

    const incoming = makeIncomingRequest(null);
    await forward(incoming, "/v1/me");

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [, init] = fetchSpy.mock.calls[0] as [Request, RequestInit];
    expect(init?.signal).toBeInstanceOf(AbortSignal);
  });

  // Outbound URL is composed from GATEWAY_URL + path
  it("calls the gateway at GATEWAY_URL + gatewayPath", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 200 }));

    const incoming = makeIncomingRequest(null);
    await forward(incoming, "/v1/me");

    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    expect(outboundReq.url).toBe(`${TEST_GATEWAY_URL}/v1/me`);
  });

  // Gateway status is relayed
  it("relays the gateway HTTP status code", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 401 }));

    const incoming = makeIncomingRequest(null);
    const response = await forward(incoming, "/v1/me");

    expect(response.status).toBe(401);
  });

  // ─── PLATFORM-004 AC-2: ID-token injection ────────────────────────────────
  // When CLOUD_RUN_SA_IDENTITY_URL is set, forward() must fetch an ID token
  // from the metadata server and attach it as Authorization: Bearer <token>.
  // The metadata fetch is itself a call to global fetch — the spy intercepts both.

  describe("ID-token injection (PLATFORM-004 AC-2)", () => {
    const METADATA_URL =
      "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity";
    const STUB_TOKEN = "stub-google-id-token";

    beforeEach(() => {
      // Signal that we are running inside Cloud Run (metadata server available).
      process.env["CLOUD_RUN_SA_IDENTITY_URL"] = METADATA_URL;
    });

    afterEach(() => {
      delete process.env["CLOUD_RUN_SA_IDENTITY_URL"];
    });

    // AC-2: when CLOUD_RUN_SA_IDENTITY_URL is set, the ID token must be attached.
    it("attaches Authorization: Bearer from the metadata server when CLOUD_RUN_SA_IDENTITY_URL is set", async () => {
      // fetchSpy intercepts both calls: metadata fetch and gateway fetch.
      fetchSpy
        // First call: metadata endpoint returns the stub token.
        .mockResolvedValueOnce(new Response(STUB_TOKEN, { status: 200 }))
        // Second call: gateway returns 200.
        .mockResolvedValueOnce(new Response(null, { status: 200 }));

      const incoming = makeIncomingRequest(null);
      await forward(incoming, "/v1/me");

      // The first fetch call must be to the metadata URL with the gateway audience.
      const firstCall: Request = fetchSpy.mock.calls[0][0] as Request;
      expect(firstCall.url).toContain(METADATA_URL);
      expect(firstCall.url).toContain(encodeURIComponent(TEST_GATEWAY_URL));
      expect(firstCall.headers.get("Metadata-Flavor")).toBe("Google");

      // The second fetch call (to the gateway) must carry the ID token.
      const gatewayCall: Request = fetchSpy.mock.calls[1][0] as Request;
      expect(gatewayCall.headers.get("authorization")).toBe(
        `Bearer ${STUB_TOKEN}`
      );
    });

    // AC-2: when CLOUD_RUN_SA_IDENTITY_URL is NOT set (local/CI env), no
    // Authorization header is added and only one fetch call is made.
    it("does not add Authorization header when CLOUD_RUN_SA_IDENTITY_URL is absent", async () => {
      delete process.env["CLOUD_RUN_SA_IDENTITY_URL"];

      fetchSpy.mockResolvedValueOnce(new Response(null, { status: 200 }));

      const incoming = makeIncomingRequest(null);
      await forward(incoming, "/v1/me");

      expect(fetchSpy).toHaveBeenCalledOnce();
      const gatewayCall: Request = fetchSpy.mock.calls[0][0] as Request;
      expect(gatewayCall.headers.get("authorization")).toBeNull();
    });

    // A09: if the metadata server fetch fails, forward() must NOT silently drop
    // the error — it should propagate so the caller knows the gateway call failed.
    it("propagates metadata fetch failure without swallowing it", async () => {
      fetchSpy.mockRejectedValueOnce(new Error("metadata server unreachable"));

      const incoming = makeIncomingRequest(null);
      await expect(forward(incoming, "/v1/me")).rejects.toThrow(
        "metadata server unreachable"
      );
    });
  });
});
