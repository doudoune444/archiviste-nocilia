/**
 * Public lore-gap board — React Server Component (BOARD-002 AC1).
 *
 * BOARD-003: reads searchParams (category, sort) and passes them to the gateway
 * fetch. Active filter+sort are reflected in the URL (shareable/bookmarkable).
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
import { BoardControls } from "@/components/category-filter/BoardControls";
import { filterFromParams, buildGatewayParams } from "@/components/category-filter/params";
import { BOARD_PAGE_SIZE, isBoardPage } from "@/components/board/types";
import type { BoardPage } from "@/components/board/types";
import type { BoardFilter } from "@/components/category-filter/params";
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

async function fetchInitialBoard(filter: BoardFilter): Promise<FetchResult> {
  const req = await buildServerRequest();
  const params = buildGatewayParams(filter, BOARD_PAGE_SIZE);

  // AC1: bff-proxy is the sole gateway boundary.
  // AC5: wrap forward() + res.json() in try/catch so any throw (network error,
  // AbortSignal timeout, unset GATEWAY_URL, malformed JSON body) routes to the
  // error state rather than crashing the RSC.
  let res: Response;
  try {
    res = await forward(req, `/v1/board?${params}`);
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

interface BoardPageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function BoardPage({ searchParams }: BoardPageProps) {
  // BOARD-003 AC: read filter+sort from URL search params.
  const rawParams = await searchParams;
  const urlParams = new URLSearchParams();
  for (const [key, value] of Object.entries(rawParams)) {
    if (typeof value === "string") {
      urlParams.set(key, value);
    } else if (Array.isArray(value) && value.length > 0 && typeof value[0] === "string") {
      urlParams.set(key, value[0]);
    }
  }
  const filter = filterFromParams(urlParams);

  const result = await fetchInitialBoard(filter);

  // BOARD-003 AC: the filter/sort controls are a primary affordance and stay
  // present even when the board is empty or failed to load — the controls drive
  // a URL-param re-fetch, so they must not vanish on the error path. Categories
  // can only be derived from a successful page; empty otherwise.
  // #231: categories comes from the server — pagination- and filter-independent.
  const availableCategories = result.ok ? result.page.categories : [];

  return (
    <section className={styles.page}>
      <h1 className={styles.heading}>Tickets lore-gap</h1>
      {/* BOARD-003 AC: filter/sort controls bound to URL search params */}
      <BoardControls availableCategories={availableCategories} />
      {result.ok ? (
        <>
          <p className={styles.subtitle}>
            {result.page.total} ticket{result.page.total !== 1 ? "s" : ""} ouvert
            {result.page.total !== 1 ? "s" : ""}
          </p>
          {/* AC4: LoadMoreButton is 'use client' and handles pagination */}
          <LoadMoreButton
            initialTickets={result.page.items}
            total={result.page.total}
            filter={filter}
          />
        </>
      ) : (
        // AC5: clear error state with request id; no gateway internals leaked.
        <p className={styles.error} role="alert" data-testid="board-error">
          Impossible de charger les tickets.{" "}
          <span className={styles.requestId}>(id : {result.requestId})</span>
        </p>
      )}
    </section>
  );
}
