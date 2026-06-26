/**
 * État et métriques — RSC route (WEBOBS-001, reshelled for #346 / #347).
 *
 * AC1: renders server-side via the bff-proxy; no client-side fetch (except the
 * DepHealth client island, which polls same-origin).
 * AC5: independent cards — a failed signal never blanks the whole page.
 *
 * #347 lays the visual shell from the `v03-conv-botright` mockup: eyebrow + h1,
 * a 2×2 asymmetric metrics band slotting the four existing cards in place, and
 * a stack footer. Card contents are refactored in later slices.
 *
 * The signal fetchers live in ./fetch (Next.js 15 rejects non-reserved named
 * exports from page files); this module only declares the page component.
 */
import { headers } from "next/headers";
import { fetchStats, fetchQuality, fetchCosts } from "./fetch";
import { StatsCard } from "@/components/stats-card/StatsCard";
import { RagasGauges } from "@/components/ragas-gauges/RagasGauges";
import { CostsCard } from "@/components/costs-card/CostsCard";
import { DepHealth } from "@/components/dep-health/DepHealth";
import styles from "./page.module.css";

const FOOTER_STACK =
  "Archiviste Nocilia — Gateway Rust (Axum) · Workers Python (FastAPI / LangChain) · Persistence Markdown sur GCS";

export default async function MetriquesPage() {
  const incomingHeaders = await headers();
  const requestId = incomingHeaders.get("x-request-id") ?? crypto.randomUUID();

  const [stats, quality, costs] = await Promise.all([
    fetchStats(requestId),
    fetchQuality(requestId),
    fetchCosts(requestId),
  ]);

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <p className={styles.eyebrow}>Archiviste Nocilia · RAG public</p>
        <h1 className={styles.heading}>État et métriques</h1>
      </header>

      <div className={styles.band}>
        <div className={styles.slotRagas}>
          <RagasGauges quality={quality} />
        </div>
        <div className={styles.slotDeps}>
          <DepHealth />
        </div>
        <div className={styles.slotCosts}>
          <CostsCard costs={costs} />
        </div>
        <div className={styles.slotConversations}>
          <StatsCard stats={stats} />
        </div>
      </div>

      <footer className={styles.footer}>{FOOTER_STACK}</footer>
    </div>
  );
}
