/**
 * auth-forms — validation helpers and gateway-status-to-French mapping.
 *
 * AUTH-001:
 * - mapGatewayStatusToMessage: translates gateway HTTP status + body to
 *   a human-readable French message for login. Behavior-rich unit-test core.
 * - isPasswordLongEnough: client-side guard before form submission.
 *
 * AUTH-002:
 * - mapSignupStatusToMessage: signup-aware variant that adds a 409 branch
 *   (email already registered) on top of the shared mapping behavior.
 *
 * A09: raw gateway response bodies are never logged; only structured codes.
 */

/** Minimum password length enforced by the gateway (auth.rs PASSWORD_MIN_LEN). */
export const PASSWORD_MIN_LEN = 12;

/** Opaque error shape from the gateway auth routes. */
interface GatewayAuthError {
  error: string;
  retry_after_seconds?: number;
}

/** Result of mapping a gateway response to a displayable French message. */
export interface GatewayStatusResult {
  /** Human-readable French message for display in the UI. */
  message: string;
  /**
   * Seconds to wait before retrying. Only present when the gateway
   * returned 429 login_throttled and included retry_after_seconds.
   */
  retryAfterSeconds?: number;
}

/**
 * Returns true if the password meets the gateway minimum length requirement.
 * Used client-side before submit to avoid a round-trip for obvious failures.
 */
export function isPasswordLongEnough(password: string): boolean {
  return password.length >= PASSWORD_MIN_LEN;
}

/**
 * Guards the ?from= redirect-back parameter against open-redirect attacks.
 *
 * WHY: `router.push(raw)` with an unvalidated ?from= param lets an attacker
 * craft `.../login?from=https://evil.com` (or the protocol-relative variant
 * `//evil.com`, or the backslash trick `/\evil.com`) to redirect victims
 * off-site after login — a classic phishing pivot (OWASP A01 / CWE-601).
 *
 * Valid target: starts with exactly one `/` and the second character is
 * neither `/` nor `\` (both are treated as protocol-relative by browsers).
 * Everything else — external URLs, protocol-relative paths, javascript:,
 * empty/null values — falls back to `/`.
 */
export function safeRedirectTarget(raw: string | null | undefined): string {
  if (typeof raw !== "string" || raw.length === 0) return "/";
  if (raw[0] !== "/") return "/";
  // Reject //evil.com and /\evil.com — both are treated as protocol-relative.
  const second = raw[1];
  if (second === "/" || second === "\\") return "/";
  return raw;
}

/**
 * Reads the `retry_after_seconds` field from a 429 body, or falls back to
 * the `Retry-After` header value parsed as an integer.
 *
 * Returns undefined when neither source provides a positive integer.
 */
function extractRetryAfter(
  body: GatewayAuthError,
  retryAfterHeader: string | null
): number | undefined {
  const fromBody = body.retry_after_seconds;
  if (typeof fromBody === "number" && fromBody > 0) return fromBody;

  if (retryAfterHeader !== null) {
    const parsed = parseInt(retryAfterHeader, 10);
    if (!isNaN(parsed) && parsed > 0) return parsed;
  }

  return undefined;
}

/**
 * Maps a gateway HTTP status code and error body to a French UI message.
 *
 * Auth error envelope shapes (gateway/src/routes/auth.rs):
 *   401 → { error: "invalid_credentials", request_id: "..." }
 *   429 → { error: "login_throttled", request_id: "...", retry_after_seconds: N }
 *         + Retry-After: N header
 *   503 → { error: "upstream_unavailable", request_id: "..." }
 *
 * A09: gateway internals (error codes, request_id) are never surfaced verbatim.
 */
export function mapGatewayStatusToMessage(
  status: number,
  body: unknown,
  retryAfterHeader: string | null
): GatewayStatusResult {
  if (status === 401) {
    return { message: "Adresse e-mail ou mot de passe incorrect." };
  }

  if (status === 429) {
    const safeBody = isGatewayAuthError(body) ? body : {};
    const retryAfterSeconds = extractRetryAfter(
      safeBody as GatewayAuthError,
      retryAfterHeader
    );
    const waitHint =
      retryAfterSeconds !== undefined
        ? ` Réessayez dans ${retryAfterSeconds.toString()} secondes.`
        : "";
    return {
      message: `Trop de tentatives échouées.${waitHint}`,
      retryAfterSeconds,
    };
  }

  if (status === 503) {
    return {
      message:
        "Le service est temporairement indisponible. Réessayez dans quelques instants.",
    };
  }

  return { message: "Une erreur inattendue s'est produite. Réessayez." };
}

/** Type guard: checks that value is a GatewayAuthError shape. */
function isGatewayAuthError(value: unknown): value is GatewayAuthError {
  if (typeof value !== "object" || value === null) return false;
  const obj = value as Record<string, unknown>;
  return typeof obj["error"] === "string";
}

/**
 * Maps a gateway HTTP status code to a French UI message for the signup flow.
 *
 * Adds a 409 branch on top of the shared login mapping:
 *   409 → { error: "email_taken" } — directs user to log in instead.
 *
 * All other status codes delegate to mapGatewayStatusToMessage so the
 * 503 / 429 / fallback messages stay byte-identical between login and signup.
 *
 * A09: gateway error codes are never surfaced verbatim.
 */
export function mapSignupStatusToMessage(
  status: number,
  body: unknown,
  retryAfterHeader: string | null
): GatewayStatusResult {
  if (status === 409) {
    return {
      message:
        "Cette adresse e-mail est déjà enregistrée. Connectez-vous.",
    };
  }

  return mapGatewayStatusToMessage(status, body, retryAfterHeader);
}
