// AC #286: BFF route DELETE /api/v1/conversations/{id} (thin proxy)
// - DELETE handler forwards to gateway /v1/conversations/{id} via forward()
// - method, archiviste cookies, and request_id propagated to the gateway
// - gateway status and body (204/404/409) relayed unchanged
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { DELETE } from "@/app/api/v1/conversations/[id]/route";

const TEST_GATEWAY_URL = "http://gateway.test:8080";
const CONVERSATION_ID = "550e8400-e29b-41d4-a716-446655440000";

function makeContext(id: string) {
  return { params: Promise.resolve({ id }) };
}

function makeDeleteRequest(cookieHeader: string | null): Request {
  const headers = new Headers();
  if (cookieHeader !== null) {
    headers.set("cookie", cookieHeader);
  }
  return new Request(
    `http://next.test/api/v1/conversations/${CONVERSATION_ID}`,
    { method: "DELETE", headers }
  );
}

describe("DELETE /api/v1/conversations/[id]", () => {
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

    await DELETE(
      makeDeleteRequest(null) as never,
      makeContext(CONVERSATION_ID)
    );

    expect(fetchSpy).toHaveBeenCalledOnce();
    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    expect(outboundReq.method).toBe("DELETE");
    expect(outboundReq.url).toBe(
      `${TEST_GATEWAY_URL}/v1/conversations/${CONVERSATION_ID}`
    );
  });

  it("propagates archiviste cookies and a request id to the gateway", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 204 }));

    await DELETE(
      makeDeleteRequest("archiviste_session=sess123; _other=x") as never,
      makeContext(CONVERSATION_ID)
    );

    const outboundReq: Request = fetchSpy.mock.calls[0][0] as Request;
    expect(outboundReq.headers.get("cookie")).toContain(
      "archiviste_session=sess123"
    );
    expect(outboundReq.headers.get("x-request-id")).toBeTruthy();
  });

  it("relays a 204 No Content from the gateway unchanged", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 204 }));

    const response = await DELETE(
      makeDeleteRequest(null) as never,
      makeContext(CONVERSATION_ID)
    );

    expect(response.status).toBe(204);
  });

  it("relays a 404 Not Found body from the gateway unchanged", async () => {
    const body = JSON.stringify({ error: "not_found" });
    fetchSpy.mockResolvedValue(
      new Response(body, {
        status: 404,
        headers: { "content-type": "application/json" },
      })
    );

    const response = await DELETE(
      makeDeleteRequest(null) as never,
      makeContext(CONVERSATION_ID)
    );

    expect(response.status).toBe(404);
    expect(await response.text()).toBe(body);
  });

  it("relays a 409 Conflict from the gateway unchanged", async () => {
    fetchSpy.mockResolvedValue(new Response(null, { status: 409 }));

    const response = await DELETE(
      makeDeleteRequest(null) as never,
      makeContext(CONVERSATION_ID)
    );

    expect(response.status).toBe(409);
  });
});
