/**
 * Next.js middleware — per-request security headers (PLATFORM-004 AC-3).
 *
 * Emits a nonce-based CSP at parity with the gateway's own header
 * (gateway/src/lib.rs:480):
 *   default-src 'self'; script-src 'self'; style-src 'self';
 *   img-src 'self' data:; object-src 'none'; frame-ancestors 'none';
 *   base-uri 'none'; form-action 'self'
 *
 * WHY middleware (not next.config.ts headers()):
 *   script-src / style-src without 'unsafe-inline' require a per-request
 *   nonce so Next.js can inject its own inline hydration scripts.  A static
 *   headers() block cannot generate a per-request nonce; middleware runs on
 *   every request and can forward the nonce to the RSC layer via a request
 *   header ('x-nonce').
 *
 * PLATFORM-001 static headers (nosniff, Referrer-Policy) are also emitted
 * here — the headers() block in next.config.ts has been removed so this
 * middleware is the single owner of all three security headers.
 *
 * WHY src/middleware.ts and not middleware.ts at project root:
 *   Next.js dev bundler sets rootDir = appDir (src/app) and scans
 *   path.join(rootDir, '..') = src/ for middleware files. The project-root
 *   location is only scanned when pagesDir is set (Pages Router projects).
 *   Because this project uses the App Router under src/app, middleware MUST
 *   live at src/middleware.ts to be picked up by both dev and production builds.
 */

import { NextRequest, NextResponse } from "next/server";

/**
 * Generate a cryptographically random nonce (16 bytes → 24-char base64url).
 * Uses the Web Crypto API available in the Next.js Edge runtime.
 */
function generateNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

/**
 * Build a CSP header value at gateway parity with a per-request nonce
 * injected into script-src and style-src.
 *
 * Gateway literal (gateway/src/lib.rs:480):
 *   default-src 'self'; script-src 'self'; style-src 'self';
 *   img-src 'self' data:; object-src 'none'; frame-ancestors 'none';
 *   base-uri 'none'; form-action 'self'
 *
 * Dev exception: `next dev` (Fast Refresh / webpack HMR) runs eval() and
 * injects un-nonced inline scripts the nonce CSP would block — killing
 * hydration. Dev therefore drops the nonce for 'unsafe-eval'/'unsafe-inline'
 * (a nonce makes the browser ignore 'unsafe-inline', so it must go). Only the
 * strict nonce branch ships: `next build` emits no eval and nonces its scripts.
 */
function buildCsp(nonce: string): string {
  const isDevelopment = process.env.NODE_ENV !== "production";
  const scriptSrc = isDevelopment
    ? "script-src 'self' 'unsafe-eval' 'unsafe-inline'"
    : `script-src 'self' 'nonce-${nonce}'`;
  const styleSrc = isDevelopment
    ? "style-src 'self' 'unsafe-inline'"
    : `style-src 'self' 'nonce-${nonce}'`;
  return [
    "default-src 'self'",
    scriptSrc,
    styleSrc,
    "img-src 'self' data:",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
  ].join("; ");
}

export function middleware(request: NextRequest): NextResponse {
  const nonce = generateNonce();
  const csp = buildCsp(nonce);

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });

  response.headers.set("Content-Security-Policy", csp);
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  response.headers.set(
    "Strict-Transport-Security",
    "max-age=31536000; includeSubDomains; preload"
  );

  return response;
}

// Apply to all routes except Next.js internals and static assets.
// _next/static and _next/image are served by Next.js directly; adding CSP
// to those responses is harmless but wastes cycles and can confuse caches.
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
