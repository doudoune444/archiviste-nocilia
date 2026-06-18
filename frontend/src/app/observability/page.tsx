/**
 * Observability page — RSC route (WEBOBS-001).
 *
 * AC1: renders server-side via the bff-proxy; no client-side fetch.
 * AC5: independent cards — a failed signal never blanks the whole page.
 *
 * The signal fetchers live in ./fetch (Next.js 15 rejects non-reserved named
 * exports from page files); this module only declares the page component.
 */
import { headers } from "next/headers";
import { fetchStats, fetchQuality } from "./fetch";
import { StatsCard } from "@/components/stats-card/StatsCard";
import { RagasGauges } from "@/components/ragas-gauges/RagasGauges";
import styles from "./page.module.css";

export default async function ObservabilityPage() {
  const incomingHeaders = await headers();
  const requestId = incomingHeaders.get("x-request-id") ?? crypto.randomUUID();

  const [stats, quality] = await Promise.all([
    fetchStats(requestId),
    fetchQuality(requestId),
  ]);

  return (
    <section className={styles.page}>
      <h1 className={styles.heading}>Observabilité</h1>
      <div className={styles.grid}>
        <StatsCard stats={stats} />
        <RagasGauges quality={quality} />
      </div>
    </section>
  );
}
