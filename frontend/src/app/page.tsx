/**
 * Root route `/` — the Archiviste chat (#245).
 *
 * The chat surface and the conversation history are rendered by the global
 * AppShell (in the root layout) for the `/` route, sharing thread state with the
 * sidebar. This page is therefore an intentional passthrough: it adds no content
 * of its own. The old hero with the "/chat" CTA is gone.
 */

export default function AccueilPage() {
  return null;
}
