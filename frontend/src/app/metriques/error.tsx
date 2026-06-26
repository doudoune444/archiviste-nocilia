"use client";

/**
 * État et métriques — error boundary segment (WEBOBS-001, renamed #347).
 *
 * AC5: defence in depth — catches any render throw inside the metrics segment
 * and shows a generic message without leaking internals.
 * The "use client" directive is required by Next.js for error boundaries.
 */

export default function MetriquesError() {
  return (
    <section>
      <h1>État et métriques</h1>
      <p>
        Une erreur inattendue s&apos;est produite. Veuillez réessayer plus tard.
      </p>
    </section>
  );
}
