/**
 * /logout — server-side session invalidation page (AUTH-001).
 *
 * Server Component: on render, forwards a POST to the gateway logout endpoint
 * through bff-proxy (which relays the archiviste_session cookie). The gateway
 * revokes the session server-side and responds with Set-Cookie Max-Age=0 to
 * clear the browser cookie. bff-proxy relays that Set-Cookie back.
 *
 * After the gateway call, redirect() returns the user to / with a fresh RSC
 * render (the sidebar app-shell re-fetches /v1/me and shows the anonymous state).
 *
 * AC: "Se déconnecter" ends the session server-side (not JWT-only).
 * AC: header returns to anonymous after logout.
 *
 * A09: never logs cookies, tokens, or Set-Cookie values.
 */

import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { forward } from "@/lib/bff-proxy";

export default async function LogoutPage() {
  const cookieStore = await cookies();
  const headerStore = await headers();
  const requestId = headerStore.get("x-request-id") ?? undefined;

  const outboundHeaders = new Headers();
  outboundHeaders.set("content-type", "application/json");

  const cookieHeader = cookieStore.toString();
  if (cookieHeader) {
    outboundHeaders.set("cookie", cookieHeader);
  }
  if (requestId !== undefined) {
    outboundHeaders.set("x-request-id", requestId);
  }

  // Build a synthetic POST request so forward() receives the session cookie
  // and the required Content-Type: application/json header.
  const syntheticRequest = new Request("http://server/api/v1/auth/logout", {
    method: "POST",
    headers: outboundHeaders,
    body: "",
  });

  // Best-effort: if the gateway is unavailable the session cookie will expire
  // naturally. The redirect always happens so the user is never stuck here.
  await forward(syntheticRequest, "/v1/auth/logout").catch(() => undefined);

  // After gateway logout, redirect to home. The RSC re-render of the sidebar
  // app-shell in the new layout fetch will call /v1/me and get anonymous tier.
  redirect("/");
}
