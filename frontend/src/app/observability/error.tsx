"use client";

/**
 * Observability error boundary segment (WEBOBS-001).
 *
 * AC5: defence in depth — catches any render throw inside the observability
 * segment and shows a generic message without leaking internals.
 * The "use client" directive is required by Next.js for error boundaries.
 */

export default function ObservabilityError() {
  return (
    <section>
      <h1>Observabilité</h1>
      <p>
        Une erreur inattendue s&apos;est produite. Veuillez réessayer plus tard.
      </p>
    </section>
  );
}
