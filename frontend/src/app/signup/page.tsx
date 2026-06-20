"use client";
/**
 * /signup — account creation form page (AUTH-002).
 *
 * Client Component: handles form state, client-side validation, and
 * server-round-trip through the BFF API route POST /api/v1/auth/signup.
 *
 * AC: invalid email rejected client-side before submit.
 * AC: sub-minimum password rejected client-side before submit.
 * AC: 409 (email already taken) maps to a clear French message directing
 *     the user to log in ("Cette adresse e-mail est déjà enregistrée.
 *     Connectez-vous.").
 * AC: on success, user is redirected to /login to authenticate.
 * AC: password field is masked (type="password"); credentials never logged.
 *
 * A09: credentials are never logged or echoed.
 */

import { Suspense, useState, useCallback } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  isPasswordLongEnough,
  mapSignupStatusToMessage,
  safeRedirectTarget,
} from "@/lib/auth-forms";
import styles from "../login/login.module.css";

/** Minimum characters the UI validates locally (mirrors gateway PASSWORD_MIN_LEN). */
const PASSWORD_MIN_LEN = 12;

/** Simple email shape check — mirrors the gateway regex AC-3 pattern. */
function isEmailValid(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function SignupForm() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [isPending, setIsPending] = useState(false);

  /** Client-side validation — rejects before any network call. */
  const validateForm = useCallback((): boolean => {
    let valid = true;

    if (!isEmailValid(email)) {
      setEmailError("Adresse e-mail invalide.");
      valid = false;
    } else {
      setEmailError(null);
    }

    if (!isPasswordLongEnough(password)) {
      setPasswordError(
        `Le mot de passe doit contenir au moins ${PASSWORD_MIN_LEN} caractères.`
      );
      valid = false;
    } else {
      setPasswordError(null);
    }

    return valid;
  }, [email, password]);

  const handleSubmit = useCallback(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      setErrorMessage(null);

      if (!validateForm()) return;

      setIsPending(true);
      try {
        const response = await fetch("/api/v1/auth/signup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password }),
        });

        if (response.ok) {
          // AC: signup does not auto-login (gateway returns 201 without a
          // session cookie). Redirect to /login so the user can authenticate.
          // safeRedirectTarget guards the ?from= param (OWASP A01 / CWE-601).
          const from = safeRedirectTarget(searchParams.get("from"));
          const target = from === "/" ? "/login" : from;
          router.refresh();
          router.push(target);
          return;
        }

        const body: unknown = await response.json().catch(() => null);
        const retryAfterHeader = response.headers.get("retry-after");
        const { message } = mapSignupStatusToMessage(
          response.status,
          body,
          retryAfterHeader
        );
        setErrorMessage(message);
      } catch {
        // Network failure — never log the error object (may contain credentials
        // context).
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
      <h1 className={styles.heading}>Créer un compte</h1>
      <form className={styles.form} onSubmit={handleSubmit} noValidate>
        <div className={styles.field}>
          <label htmlFor="signup-email" className={styles.label}>
            Adresse e-mail
          </label>
          <input
            id="signup-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              if (emailError !== null) setEmailError(null);
            }}
            className={styles.input}
            disabled={isPending}
          />
          {emailError !== null && (
            <span className={styles.fieldError} role="alert">
              {emailError}
            </span>
          )}
        </div>
        <div className={styles.field}>
          <label htmlFor="signup-password" className={styles.label}>
            Mot de passe
          </label>
          {/* AC: password field masked — type="password" prevents echoing */}
          <input
            id="signup-password"
            type="password"
            autoComplete="new-password"
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
          {isPending ? "Création…" : "Créer un compte"}
        </button>
      </form>
      <p className={styles.switchLink}>
        <Link href="/login">J&apos;ai déjà un compte</Link>
      </p>
    </section>
  );
}

/**
 * Next.js requires components calling useSearchParams() to sit under a Suspense
 * boundary, otherwise the static prerender of this client page bails the build.
 * The fallback renders the static shell so layout stays stable until hydration.
 */
export default function SignupPage() {
  return (
    <Suspense
      fallback={
        <section className={styles.container}>
          <h1 className={styles.heading}>Créer un compte</h1>
        </section>
      }
    >
      <SignupForm />
    </Suspense>
  );
}
