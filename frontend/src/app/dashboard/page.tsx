/**
 * Author-gated dashboard — React Server Component (DASH-001).
 *
 * AC-1: an author session reaches the page; it fetches tickets via
 *       /api/v1/tickets (BFF) → gateway /v1/tickets (RequireAuthor).
 * AC-2: a non-author caller receives the gateway 401/403; the page renders
 *       a clean "réservé à l'auteur" message — NOT a broken page or stack trace.
 * AC-3: reuses TicketTable, ConfirmationBadge, LoadMoreButton, BoardControls,
 *       category-filter/params.ts, BOARD_PAGE_SIZE from the board modules.
 * AC-4: list-load failure shows a clear error state INCLUDING the request id.
 * AC-5: no DASH-002 per-row affordance here (out of scope).
 */

import { cookies, headers } from "next/headers";
import { forward } from "@/lib/bff-proxy";
import { DashboardTickets } from "@/components/dashboard/DashboardTickets";
import { BoardControls } from "@/components/category-filter/BoardControls";
import {
  filterFromParams,
  buildGatewayParams,
} from "@/components/category-filter/params";
import { BOARD_PAGE_SIZE, isBoardPage } from "@/components/board/types";
import type { BoardPage } from "@/components/board/types";
import type { BoardFilter } from "@/components/category-filter/params";
import styles from "./page.module.css";

/** HTTP status codes that mean the caller is not an author (AC-2). */
const AUTHOR_REQUIRED_STATUSES = new Set([401, 403]);

type FetchResult =
  | { ok: true; page: BoardPage }
  | { ok: false; forbidden: true }
  | { ok: false; forbidden: false; requestId: string };

/** Builds a synthetic Request so forward() can extract cookies/request-id. */
async function buildServerRequest(): Promise<Request> {
  const cookieStore = await cookies();
  const headerStore = await headers();

  const outHeaders = new Headers();
  const cookieHeader = cookieStore.toString();
  if (cookieHeader) {
    outHeaders.set("cookie", cookieHeader);
  }
  const requestId = headerStore.get("x-request-id");
  if (requestId !== null) {
    outHeaders.set("x-request-id", requestId);
  }
  return new Request("http://localhost/api/v1/tickets", { headers: outHeaders });
}

async function fetchInitialTickets(filter: BoardFilter): Promise<FetchResult> {
  const req = await buildServerRequest();
  const params = buildGatewayParams(filter, BOARD_PAGE_SIZE);

  let res: Response;
  try {
    res = await forward(req, `/v1/tickets?${params}`);
  } catch {
    // AC-4: forward() threw — GATEWAY_URL unset, network failure, or timeout.
    return { ok: false, forbidden: false, requestId: crypto.randomUUID() };
  }

  // AC-2: 401/403 means caller is not an author — clean refusal, not broken page.
  if (AUTHOR_REQUIRED_STATUSES.has(res.status)) {
    return { ok: false, forbidden: true };
  }

  if (!res.ok) {
    const requestId = res.headers.get("x-request-id") ?? crypto.randomUUID();
    return { ok: false, forbidden: false, requestId };
  }

  try {
    const body: unknown = await res.json();
    if (!isBoardPage(body)) {
      const requestId = res.headers.get("x-request-id") ?? crypto.randomUUID();
      return { ok: false, forbidden: false, requestId };
    }
    return { ok: true, page: body };
  } catch {
    const requestId = res.headers.get("x-request-id") ?? crypto.randomUUID();
    return { ok: false, forbidden: false, requestId };
  }
}

interface DashboardPageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function DashboardPage({
  searchParams,
}: DashboardPageProps) {
  // Read filter+sort from URL search params (reuses category-filter/params.ts).
  const rawParams = await searchParams;
  const urlParams = new URLSearchParams();
  for (const [key, value] of Object.entries(rawParams)) {
    if (typeof value === "string") {
      urlParams.set(key, value);
    } else if (
      Array.isArray(value) &&
      value.length > 0 &&
      typeof value[0] === "string"
    ) {
      urlParams.set(key, value[0]);
    }
  }
  const filter = filterFromParams(urlParams);

  const result = await fetchInitialTickets(filter);

  // AC-2: clean refusal for non-author callers.
  if (!result.ok && result.forbidden) {
    return (
      <section className={styles.page}>
        <h1 className={styles.heading}>Tableau de bord</h1>
        <p className={styles.forbidden} role="status">
          Cette page est réservée à l&apos;auteur. Connectez-vous avec un
          compte auteur pour y accéder.
        </p>
      </section>
    );
  }

  // #231: categories comes from the server — pagination- and filter-independent.
  const availableCategories = result.ok ? result.page.categories : [];

  return (
    <section className={styles.page}>
      <h1 className={styles.heading}>Tableau de bord</h1>
      {/* AC-3: reuse BoardControls for filter/sort URL params */}
      <BoardControls availableCategories={availableCategories} />
      {result.ok ? (
        <>
          <p className={styles.subtitle}>
            {result.page.total} ticket{result.page.total !== 1 ? "s" : ""}{" "}
            ouvert{result.page.total !== 1 ? "s" : ""}
          </p>
          {/* AC-3: reuse LoadMoreButton via DashboardTickets (which owns drawer state — DASH-002) */}
          <DashboardTickets
            initialTickets={result.page.items}
            total={result.page.total}
            filter={filter}
            apiPath="/api/v1/tickets"
          />
        </>
      ) : (
        // AC-4: clear error state with request id; no gateway internals leaked.
        <p
          className={styles.error}
          role="alert"
          data-testid="dashboard-error"
        >
          Impossible de charger les tickets.{" "}
          <span className={styles.requestId}>
            (id&nbsp;: {result.requestId})
          </span>
        </p>
      )}
    </section>
  );
}
