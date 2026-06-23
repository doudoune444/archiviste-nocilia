/**
 * Server-side identity fetch for the sidebar app-shell (#248).
 *
 * Reads GET /v1/me through the bff-proxy using the forwarded browser cookies
 * (archiviste_session / archiviste_anon). Identity is never client-supplied (A01).
 *
 * Degrades gracefully to the anonymous fallback on ANY failure — network error,
 * non-OK status, JSON parse error, or unexpected shape. The layout must never
 * crash because of a bad /v1/me response, matching the former AuthAwareNav.
 */

import { cookies, headers } from "next/headers";
import { forward } from "@/lib/bff-proxy";
import type { Identity } from "./identity";

const VALID_TIERS: ReadonlySet<string> = new Set([
  "anonymous",
  "member",
  "author",
]);

function isIdentity(value: unknown): value is Identity {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  if (!VALID_TIERS.has(candidate["tier"] as string)) return false;
  if (candidate["email"] !== null && typeof candidate["email"] !== "string") {
    return false;
  }
  return true;
}

export async function fetchIdentity(): Promise<Identity> {
  const anonymous: Identity = { tier: "anonymous", email: null };

  try {
    const cookieStore = await cookies();
    const headerStore = await headers();
    const requestId = headerStore.get("x-request-id");

    const outboundHeaders = new Headers();
    const cookieHeader = cookieStore.toString();
    if (cookieHeader) {
      outboundHeaders.set("cookie", cookieHeader);
    }
    if (requestId !== null) {
      outboundHeaders.set("x-request-id", requestId);
    }

    const syntheticRequest = new Request("http://server/api/v1/me", {
      headers: outboundHeaders,
    });

    const response = await forward(syntheticRequest, "/v1/me");
    if (!response.ok) return anonymous;

    const body: unknown = await response.json();
    return isIdentity(body) ? body : anonymous;
  } catch {
    return anonymous;
  }
}
