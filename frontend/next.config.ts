import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // CSP via headers — PLATFORM-001 AC: at least as strict as gateway.
  // Hardened nonce-based CSP is deferred to PLATFORM-004 (#193) because
  // Next.js inline scripts (React hydration) require a per-request nonce
  // injected through middleware, which is out of scope for this bootstrap slice.
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          {
            key: "Content-Security-Policy",
            value:
              "default-src 'self'; object-src 'none'; frame-ancestors 'none'",
          },
          {
            key: "X-Content-Type-Options",
            value: "nosniff",
          },
          {
            key: "Referrer-Policy",
            value: "strict-origin-when-cross-origin",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
