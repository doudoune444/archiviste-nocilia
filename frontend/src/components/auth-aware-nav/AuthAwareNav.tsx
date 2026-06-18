/**
 * AuthAwareNav — persistent server component rendered in the global layout.
 *
 * PLATFORM-003:
 * - Reads GET /v1/me through the bff-proxy (server-side only).
 * - Renders static view links for ALL tiers.
 * - Renders a tier-driven auth cluster on the right.
 * - Degrades gracefully to the anonymous variant on ANY fetch/parse failure.
 *   The layout must never crash because of a bad /v1/me response.
 *
 * A09: email is rendered as TEXT only; never logged.
 */

import Link from "next/link";
import { cookies, headers } from "next/headers";
import { forward } from "@/lib/bff-proxy";
import styles from "./AuthAwareNav.module.css";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Tier = "anonymous" | "member" | "author";

interface MeResponse {
  tier: Tier;
  email: string | null;
}

const VALID_TIERS: ReadonlySet<string> = new Set([
  "anonymous",
  "member",
  "author",
]);

/** Runtime guard: rejects anything that is not a well-shaped MeResponse. */
function isMeResponse(value: unknown): value is MeResponse {
  if (typeof value !== "object" || value === null) return false;
  const obj = value as Record<string, unknown>;
  if (!VALID_TIERS.has(obj["tier"] as string)) return false;
  if (obj["email"] !== null && typeof obj["email"] !== "string") return false;
  return true;
}

// ---------------------------------------------------------------------------
// Data fetch
// ---------------------------------------------------------------------------

/**
 * Fetch identity from the gateway via bff-proxy.
 * Returns the anonymous fallback on any failure — never throws.
 */
async function fetchIdentity(): Promise<MeResponse> {
  const anonymous: MeResponse = { tier: "anonymous", email: null };

  try {
    // Build a synthetic server-side Request carrying the browser cookies
    // so the bff-proxy can forward archiviste_session / archiviste_anon.
    const cookieStore = await cookies();
    const headerStore = await headers();
    const requestId = headerStore.get("x-request-id") ?? undefined;

    const outboundHeaders = new Headers();
    const cookieHeader = cookieStore.toString();
    if (cookieHeader) {
      outboundHeaders.set("cookie", cookieHeader);
    }
    if (requestId !== undefined) {
      outboundHeaders.set("x-request-id", requestId);
    }

    const syntheticRequest = new Request("http://server/api/v1/me", {
      headers: outboundHeaders,
    });

    const response = await forward(syntheticRequest, "/v1/me");
    if (!response.ok) return anonymous;

    const body: unknown = await response.json();
    return isMeResponse(body) ? body : anonymous;
  } catch {
    // Fail-soft: any network error, JSON parse error, or unexpected shape
    // degrades to anonymous. The nav must never crash the whole layout.
    return anonymous;
  }
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ViewLinks() {
  return (
    <ul className={styles.navLinks}>
      <li>
        <Link href="/" className={styles.navLink}>
          Chat
        </Link>
      </li>
      <li>
        <Link href="/board" className={styles.navLink}>
          Board
        </Link>
      </li>
      <li>
        <Link href="/observability" className={styles.navLink}>
          Observabilité
        </Link>
      </li>
    </ul>
  );
}

interface AuthClusterProps {
  identity: MeResponse;
}

function AuthCluster({ identity }: AuthClusterProps) {
  const { tier, email } = identity;

  if (tier === "anonymous") {
    return (
      <div className={styles.authCluster}>
        <Link href="/signup" className={styles.authLink}>
          S&apos;inscrire
        </Link>
        <Link href="/login" className={styles.authLinkPrimary}>
          Se connecter
        </Link>
      </div>
    );
  }

  return (
    <div className={styles.authCluster}>
      {tier === "author" && (
        <Link href="/dashboard" className={styles.navLink}>
          Dashboard
        </Link>
      )}
      {email !== null && (
        <span className={styles.userEmail}>{email}</span>
      )}
      <Link href="/logout" className={styles.authLink}>
        Se déconnecter
      </Link>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Nav component
// ---------------------------------------------------------------------------

/** Server component: reads identity and renders the full navigation. */
export default async function AuthAwareNav() {
  const identity = await fetchIdentity();

  return (
    <nav className={styles.nav} aria-label="Navigation principale">
      <Link href="/" className={styles.navBrand}>
        Archiviste Nocilia
      </Link>
      <ViewLinks />
      <AuthCluster identity={identity} />
    </nav>
  );
}
