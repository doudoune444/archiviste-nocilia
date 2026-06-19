"use client";
/**
 * /login — authentication form page (AUTH-001).
 *
 * Client Component: handles form state, client-side validation, and
 * server-round-trip through the BFF API route POST /api/v1/auth/login.
 *
 * AC: sub-minimum password rejected client-side before submit.
 * AC: 401/429/503 each map to a clear French message.
 * AC: on success, user is redirected to the originating page (default /).
 * AC: password field is masked (type="password"); credentials never logged.
 *
 * A09: credentials are never logged or echoed.
 */

import { Suspense, useState, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { isPasswordLongEnough, mapGatewayStatusToMessage, safeRedirectTarget } from "@/lib/auth-forms";
import styles from "./login.module.css";

/** Minimum characters the UI validates locally (mirrors gateway PASSWORD_MIN_LEN). */
const PASSWORD_MIN_LEN = 12;

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [isPending, setIsPending] = useState(false);

  /** Client-side password length check — rejects before any network call. */
  const validateForm = useCallback((): boolean => {
    if (!isPasswordLongEnough(password)) {
      setPasswordError(
        `Le mot de passe doit contenir au moins ${PASSWORD_MIN_LEN} caractères.`
      );
      return false;
    }
    setPasswordError(null);
    return true;
  }, [password]);

  const handleSubmit = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      setErrorMessage(null);

      if (!validateForm()) return;

      setIsPending(true);
      try {
        const response = await fetch("/api/v1/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password }),
        });

        if (response.ok) {
          // AC: return to originating page (default /)
          // safeRedirectTarget rejects external URLs, protocol-relative paths,
          // and backslash tricks — prevents open-redirect phishing pivot.
          const from = safeRedirectTarget(searchParams.get("from"));
          router.refresh();
          router.push(from);
          return;
        }

        const body: unknown = await response.json().catch(() => null);
        const retryAfterHeader = response.headers.get("retry-after");
        const { message } = mapGatewayStatusToMessage(
          response.status,
          body,
          retryAfterHeader
        );
        setErrorMessage(message);
      } catch {
        // Network failure — never log the error object (may contain credentials context).
        setErrorMessage(
          "Le service est temporairement indisponible. Réessayez dans quelques instants."
        );
      } finally {
        setIsPending(false);
      }
    },
    [email, password, router, searchParams, validateForm]
  );

  return (
    <section className={styles.container}>
      <h1 className={styles.heading}>Se connecter</h1>
      <form className={styles.form} onSubmit={handleSubmit} noValidate>
        <div className={styles.field}>
          <label htmlFor="login-email" className={styles.label}>
            Adresse e-mail
          </label>
          <input
            id="login-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={styles.input}
            disabled={isPending}
          />
        </div>
        <div className={styles.field}>
          <label htmlFor="login-password" className={styles.label}>
            Mot de passe
          </label>
          {/* AC: password field masked — type="password" prevents echoing */}
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              // Clear per-field error on each keystroke so it disappears as
              // the user types toward the minimum length.
              if (passwordError !== null) setPasswordError(null);
            }}
            className={styles.input}
            disabled={isPending}
          />
          {passwordError !== null && (
            <span className={styles.fieldError} role="alert">
              {passwordError}
            </span>
          )}
        </div>
        {errorMessage !== null && (
          <p className={styles.errorMessage} role="alert">
            {errorMessage}
          </p>
        )}
        <button
          type="submit"
          className={styles.submitButton}
          disabled={isPending}
        >
          {isPending ? "Connexion…" : "Se connecter"}
        </button>
      </form>
    </section>
  );
}

/**
 * Next.js requires components calling useSearchParams() to sit under a Suspense
 * boundary, otherwise the static prerender of this client page bails the build.
 * The fallback renders the static shell so layout stays stable until hydration.
 */
export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <section className={styles.container}>
          <h1 className={styles.heading}>Se connecter</h1>
        </section>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
