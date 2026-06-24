// AC #286: BFF route DELETE /api/v1/conversations/{id} (thin proxy)
//
// The route is a thin proxy: it awaits the Next.js 15 params Promise and calls
// forward(request, `/v1/conversations/${id}`). Cookie / request-id propagation
// and status/body relay are owned by forward() (covered by bff-proxy.test.ts),
// so here we verify the route's own behavior: it forwards the DELETE method to
// the correct gateway path and relays the gateway's status/body unchanged.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { NextRequest } from "next/server";
import { DELETE } from "@/app/api/v1/conversations/[id]/route";

const TEST_GATEWAY_URL = "http://gateway.test:8080";

const CONVERSATION_ID = "11111111-2222-4333-8444-555555555555";

function makeDeleteRequest(cookieHeader: string | null): NextRequest {
  const headers = new Headers();
  if (cookieHeader !== null) {
    headers.set("cookie", cookieHeader);
  }
  return new NextRequest(
    `http://next.test/api/v1/conversations/${CONVERSATION_ID}`,
    { method: "DELETE", headers }
  );
}

function makeContext(id: string): { params: Promise<{ id: string }> } {
  return { params: Promise.resolve({ id }) };
}

describe("DELETE /api/v1/conversations/[id] — thin proxy (#286)", () => {
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

  // AC: DELETE handler forwards the DELETE method to /v1/conversations/{id}.
  it("forwards DELETE to the gateway at /v1/conversations/{id}", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 204 }));

    const request = makeDeleteRequest(null);
    await DELETE(request, makeContext(CONVERSATION_ID));

    expect(fetchSpy).toHaveBeenCalledOnce();
    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    expect(outboundReq.method).toBe("DELETE");
    expect(outboundReq.url).toBe(
      `${TEST_GATEWAY_URL}/v1/conversations/${CONVERSATION_ID}`
    );
  });

  // AC: id comes from the awaited Next.js 15 params Promise, not a header.
  it("uses the conversation id from the awaited params", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 204 }));

    const otherId = "99999999-8888-4777-8666-555555555555";
    const request = makeDeleteRequest(null);
    await DELETE(request, makeContext(otherId));

    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    expect(outboundReq.url).toBe(
      `${TEST_GATEWAY_URL}/v1/conversations/${otherId}`
    );
  });

  // AC: archiviste cookies are propagated to the gateway (inherited from forward).
  it("propagates archiviste cookies to the gateway", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 204 }));

    const request = makeDeleteRequest(
      "archiviste_session=sess123; archiviste_anon=anon456"
    );
    await DELETE(request, makeContext(CONVERSATION_ID));

    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    const cookie = outboundReq.headers.get("cookie");
    expect(cookie).toContain("archiviste_session=sess123");
    expect(cookie).toContain("archiviste_anon=anon456");
  });

  // AC: a request_id is propagated to the gateway (inherited from forward).
  it("propagates an x-request-id to the gateway", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 204 }));

    const request = makeDeleteRequest(null);
    await DELETE(request, makeContext(CONVERSATION_ID));

    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    expect(outboundReq.headers.get("x-request-id")).toBeTruthy();
  });

  // AC: gateway 204 is relayed unchanged.
  it("relays the gateway 204 status unchanged", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 204 }));

    const request = makeDeleteRequest(null);
    const response = await DELETE(request, makeContext(CONVERSATION_ID));

    expect(response.status).toBe(204);
  });

  // AC: gateway 404 (not owned / nonexistent) is relayed unchanged.
  it("relays the gateway 404 status unchanged", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 404 }));

    const request = makeDeleteRequest(null);
    const response = await DELETE(request, makeContext(CONVERSATION_ID));

    expect(response.status).toBe(404);
  });

  // AC: gateway 409 (ticket conflict) status and body are relayed unchanged.
  it("relays the gateway 409 status and body unchanged", async () => {
    const body = JSON.stringify({ error: "conversation_has_ticket" });
    fetchSpy.mockResolvedValue(
      new Response(body, {
        status: 409,
        headers: { "content-type": "application/json" },
      })
    );

    const request = makeDeleteRequest(null);
    const response = await DELETE(request, makeContext(CONVERSATION_ID));

    expect(response.status).toBe(409);
    const parsed: unknown = await response.json();
    expect(parsed).toMatchObject({ error: "conversation_has_ticket" });
  });
});
