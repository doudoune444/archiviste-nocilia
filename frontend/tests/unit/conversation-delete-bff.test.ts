// AC #286: BFF route DELETE /api/v1/conversations/{id} (thin proxy)
//
// The route is a thin proxy with no business logic of its own: it forwards the
// incoming request via forward(...) to the gateway /v1/conversations/{id},
// relaying method, cookies, request_id, and the gateway's status/body unchanged
// (PRD #282 — Testing Decisions: covered by the generic bff-proxy pattern,
// method + headers/cookies forwarded).
//
// These tests drive the real DELETE handler (the public interface a browser
// hits), stubbing only global fetch — the single gateway boundary.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { NextRequest } from "next/server";
import { DELETE } from "@/app/api/v1/conversations/[id]/route";

const TEST_GATEWAY_URL = "http://gateway.test:8080";
const CONVERSATION_ID = "11111111-2222-4333-8444-555566667777";

// The handler only reads the Web Request interface (method/headers/body) via
// forward(). NextRequest extends Request, so a plain Request is structurally
// sufficient at runtime; the cast satisfies the handler's NextRequest param.
function makeDeleteRequest(
  cookieHeader: string | null,
  requestId?: string
): NextRequest {
  const headers = new Headers();
  if (cookieHeader !== null) {
    headers.set("cookie", cookieHeader);
  }
  if (requestId !== undefined) {
    headers.set("x-request-id", requestId);
  }
  return new Request(
    `http://next.test/api/v1/conversations/${CONVERSATION_ID}`,
    { method: "DELETE", headers }
  ) as unknown as NextRequest;
}

function makeContext(id: string): { params: Promise<{ id: string }> } {
  return { params: Promise.resolve({ id }) };
}

describe("DELETE /api/v1/conversations/[id] (BFF thin proxy, #286)", () => {
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

  it("forwards a DELETE to the gateway at /v1/conversations/{id}", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 204 }));

    const request = makeDeleteRequest(null);
    await DELETE(request, makeContext(CONVERSATION_ID));

    expect(fetchSpy).toHaveBeenCalledOnce();
    const outbound: Request = fetchSpy.mock.calls[0][0] as Request;
    expect(outbound.method).toBe("DELETE");
    expect(outbound.url).toBe(
      `${TEST_GATEWAY_URL}/v1/conversations/${CONVERSATION_ID}`
    );
  });

  it("propagates archiviste cookies to the gateway", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 204 }));

    const request = makeDeleteRequest(
      "archiviste_session=sess123; archiviste_anon=anon456; _other=x"
    );
    await DELETE(request, makeContext(CONVERSATION_ID));

    const outbound: Request = fetchSpy.mock.calls[0][0] as Request;
    const cookie = outbound.headers.get("cookie");
    expect(cookie).toContain("archiviste_session=sess123");
    expect(cookie).toContain("archiviste_anon=anon456");
  });

  it("reuses the incoming x-request-id on the gateway call", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 204 }));

    const request = makeDeleteRequest(null, "req-del-286");
    await DELETE(request, makeContext(CONVERSATION_ID));

    const outbound: Request = fetchSpy.mock.calls[0][0] as Request;
    expect(outbound.headers.get("x-request-id")).toBe("req-del-286");
  });

  it("relays a 204 success status unchanged", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 204 }));

    const response = await DELETE(
      makeDeleteRequest(null),
      makeContext(CONVERSATION_ID)
    );

    expect(response.status).toBe(204);
  });

  it("relays a 404 (not owned / inexistant) unchanged", async () => {
    fetchSpy.mockResolvedValue(
      new Response(JSON.stringify({ error: "not_found" }), {
        status: 404,
        headers: { "content-type": "application/json" },
      })
    );

    const response = await DELETE(
      makeDeleteRequest("archiviste_session=other"),
      makeContext(CONVERSATION_ID)
    );

    expect(response.status).toBe(404);
    const body: unknown = await response.json();
    expect(body).toMatchObject({ error: "not_found" });
  });

  it("relays a 409 (conversation carries a ticket) and its body unchanged", async () => {
    const conflictBody = JSON.stringify({ error: "conversation_has_ticket" });
    fetchSpy.mockResolvedValue(
      new Response(conflictBody, {
        status: 409,
        headers: { "content-type": "application/json" },
      })
    );

    const response = await DELETE(
      makeDeleteRequest("archiviste_session=owner"),
      makeContext(CONVERSATION_ID)
    );

    expect(response.status).toBe(409);
    const body: unknown = await response.json();
    expect(body).toMatchObject({ error: "conversation_has_ticket" });
  });

  it("uses the awaited id from the route params, not a client header", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 204 }));

    const otherId = "99999999-8888-4777-8666-555544443333";
    await DELETE(makeDeleteRequest(null), makeContext(otherId));

    const outbound: Request = fetchSpy.mock.calls[0][0] as Request;
    expect(outbound.url).toBe(
      `${TEST_GATEWAY_URL}/v1/conversations/${otherId}`
    );
  });
});
