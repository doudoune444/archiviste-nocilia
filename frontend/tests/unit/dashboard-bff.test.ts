// AC DASH-001: dashboard BFF route forwarding
//
// AC-1: GET /api/v1/tickets forwards to gateway /v1/tickets with query string verbatim.
// AC-2: the BFF relays 401/403 from the gateway so the RSC can detect the forbidden state.
// AC-4: load-failure response is relayed with x-request-id so the error state can show it.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { forward } from "@/lib/bff-proxy";

const TEST_GATEWAY_URL = "http://gateway.test:8080";

function makeIncomingRequest(cookieHeader: string | null): Request {
  const headers = new Headers();
  if (cookieHeader !== null) {
    headers.set("cookie", cookieHeader);
  }
  return new Request("http://next.test/api/v1/tickets?limit=20&offset=0", {
    headers,
  });
}

describe("dashboard BFF — forward() to /v1/tickets (DASH-001)", () => {
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    process.env["GATEWAY_URL"] = TEST_GATEWAY_URL;
    fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env["GATEWAY_URL"];
  });

  // AC-1: query string is forwarded verbatim to the gateway
  it("forwards query string verbatim to gateway /v1/tickets", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 200 }));

    const incoming = makeIncomingRequest(null);
    await forward(incoming, "/v1/tickets?limit=20&offset=0");

    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    expect(outboundReq.url).toBe(
      `${TEST_GATEWAY_URL}/v1/tickets?limit=20&offset=0`
    );
  });

  // AC-1: session cookie is forwarded so the gateway can authenticate the author
  it("forwards archiviste_session cookie so gateway can authenticate author", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 200 }));

    const incoming = makeIncomingRequest("archiviste_session=author-token");
    await forward(incoming, "/v1/tickets?limit=20&offset=0");

    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    expect(outboundReq.headers.get("cookie")).toContain(
      "archiviste_session=author-token"
    );
  });

  // AC-2: 403 from gateway is relayed so the RSC can detect forbidden state
  it("relays 403 from gateway — RSC detects forbidden state", async () => {
    fetchSpy.mockResolvedValue(
      new Response(JSON.stringify({ error: "author_required" }), {
        status: 403,
        headers: { "content-type": "application/json" },
      })
    );

    const incoming = makeIncomingRequest(null);
    const response = await forward(incoming, "/v1/tickets?limit=20&offset=0");

    expect(response.status).toBe(403);
  });

  // AC-2: 401 from gateway is relayed for unauthenticated caller
  it("relays 401 from gateway — RSC detects unauthenticated state", async () => {
    fetchSpy.mockResolvedValue(
      new Response(JSON.stringify({ error: "invalid_token" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      })
    );

    const incoming = makeIncomingRequest(null);
    const response = await forward(incoming, "/v1/tickets?limit=20&offset=0");

    expect(response.status).toBe(401);
  });

  // AC-4: x-request-id is present on error responses so the UI can display it.
  // bff-proxy always sets the outbound request-id (generated or reused from the
  // incoming request) — it does NOT relay the gateway's own x-request-id header
  // verbatim. The response always carries the BFF-owned request-id.
  it("returns a non-empty x-request-id on error response", async () => {
    fetchSpy.mockResolvedValue(
      new Response(null, {
        status: 500,
      })
    );

    const incoming = new Request(
      "http://next.test/api/v1/tickets?limit=20&offset=0",
      { headers: new Headers({ "x-request-id": "bff-req-dash-001" }) }
    );
    const response = await forward(incoming, "/v1/tickets?limit=20&offset=0");

    // bff-proxy reuses the incoming request-id on the response.
    expect(response.headers.get("x-request-id")).toBe("bff-req-dash-001");
  });

  // AC-1: successful response body is passed through
  it("relays a 200 JSON response body from gateway", async () => {
    const body = JSON.stringify({
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
    });
    fetchSpy.mockResolvedValue(
      new Response(body, {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    );

    const incoming = makeIncomingRequest(
      "archiviste_session=author-token"
    );
    const response = await forward(incoming, "/v1/tickets?limit=20&offset=0");

    expect(response.status).toBe(200);
    const parsed: unknown = await response.json();
    expect(parsed).toMatchObject({ total: 0, items: [] });
  });
});
