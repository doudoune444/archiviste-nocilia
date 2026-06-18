/**
 * Public lore-gap board — React Server Component (BOARD-002 AC1).
 *
 * Initial render is fully server-side: fetches open tickets through the
 * bff-proxy (the SOLE gateway boundary) and renders them in TicketTable.
 * The LoadMoreButton client component handles subsequent pagination (AC4).
 *
 * Error state (AC5): shows a generic message + request-id. Never leaks
 * gateway internals.
 */

import { cookies, headers } from "next/headers";
import { forward } from "@/lib/bff-proxy";
import { LoadMoreButton } from "@/components/board/LoadMoreButton";
import { BOARD_PAGE_SIZE, isBoardPage } from "@/components/board/types";
import type { BoardPage } from "@/components/board/types";
import styles from "./page.module.css";

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
  return new Request("http://localhost/api/v1/board", { headers: outHeaders });
}

type FetchResult =
  | { ok: true; page: BoardPage }
  | { ok: false; requestId: string };

async function fetchInitialBoard(): Promise<FetchResult> {
  const req = await buildServerRequest();
  const params = `?sort=priority&limit=${BOARD_PAGE_SIZE}&offset=0`;

  // AC1: bff-proxy is the sole gateway boundary.
  // AC5: wrap forward() + res.json() in try/catch so any throw (network error,
  // AbortSignal timeout, unset GATEWAY_URL, malformed JSON body) routes to the
  // error state rather than crashing the RSC.
  let res: Response;
  try {
    res = await forward(req, `/v1/board${params}`);
  } catch {
    // AC5: forward() threw (GATEWAY_URL unset, network failure, timeout).
    // No response is available — generate a fallback request id.
    return { ok: false, requestId: crypto.randomUUID() };
  }

  // AC5: non-2xx response from the gateway.
  if (!res.ok) {
    const requestId = res.headers.get("x-request-id") ?? crypto.randomUUID();
    return { ok: false, requestId };
  }

  // AC5: guard against a shape-drifted 200 body to prevent undefined.map() crash.
  try {
    const body: unknown = await res.json();
    if (!isBoardPage(body)) {
      const requestId = res.headers.get("x-request-id") ?? crypto.randomUUID();
      return { ok: false, requestId };
    }
    return { ok: true, page: body };
  } catch {
    const requestId = res.headers.get("x-request-id") ?? crypto.randomUUID();
    return { ok: false, requestId };
  }
}

export default async function BoardPage() {
  const result = await fetchInitialBoard();

  if (!result.ok) {
    // AC5: clear error state with request id; no gateway internals leaked.
    return (
      <section className={styles.page}>
        <h1 className={styles.heading}>Tickets lore-gap</h1>
        <p className={styles.error} role="alert" data-testid="board-error">
          Impossible de charger les tickets.{" "}
          <span className={styles.requestId}>
            (id : {result.requestId})
          </span>
        </p>
      </section>
    );
  }

  const { page } = result;

  return (
    <section className={styles.page}>
      <h1 className={styles.heading}>Tickets lore-gap</h1>
      <p className={styles.subtitle}>
        {page.total} ticket{page.total !== 1 ? "s" : ""} ouvert
        {page.total !== 1 ? "s" : ""}
      </p>
      {/* AC4: LoadMoreButton is 'use client' and handles pagination */}
      <LoadMoreButton
        initialTickets={page.items}
        total={page.total}
      />
    </section>
  );
}
