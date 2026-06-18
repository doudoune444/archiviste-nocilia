import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // PLATFORM-004: standalone output packages only the files needed to run
  // `next start` in the Docker image. Combined with the runtime stage copying
  // .next/standalone + .next/static + public, this produces a minimal image.
  output: "standalone",

  // CSP is now emitted per-request from middleware.ts (PLATFORM-004 AC-3) so
  // that a nonce can be injected into script-src and style-src to satisfy the
  // "no unsafe-inline" requirement at parity with the gateway's own CSP.
  // The headers() block is intentionally removed — middleware takes ownership.
};

export default nextConfig;
