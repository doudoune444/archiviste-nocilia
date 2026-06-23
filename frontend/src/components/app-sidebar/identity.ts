/**
 * Identity shape shared between the server-side fetch and the client sidebar.
 *
 * A01: identity is the cookie forwarded by the bff-proxy, never client-supplied.
 * A09: email is rendered as text only, never logged.
 */

export type Tier = "anonymous" | "member" | "author";

export interface Identity {
  tier: Tier;
  email: string | null;
}
