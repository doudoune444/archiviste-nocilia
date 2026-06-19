/**
 * Pure mapping: board filter/sort selections ↔ URLSearchParams (BOARD-003 AC4).
 *
 * Free of React so it is trivially unit-testable with Vitest.
 * Single source of truth for the gateway param names (`category`, `sort`).
 * Accepted sort values mirror the gateway SortOrder enum (board.rs).
 */

export type SortValue = "priority" | "date";

export interface BoardFilter {
  /** Optional category exact-match string. Undefined means "no filter". */
  category: string | undefined;
  /** Sort order: "priority" (default) or "date". */
  sort: SortValue;
}

const PARAM_CATEGORY = "category";
const PARAM_SORT = "sort";
const DEFAULT_SORT: SortValue = "priority";

const VALID_SORT_VALUES = new Set<string>(["priority", "date"]);

function isValidSort(value: string): value is SortValue {
  return VALID_SORT_VALUES.has(value);
}

/**
 * Read board filter from URLSearchParams (browser or server URL).
 * Unknown / missing sort falls back to the default without throwing.
 */
export function filterFromParams(params: URLSearchParams): BoardFilter {
  const rawCategory = params.get(PARAM_CATEGORY);
  const rawSort = params.get(PARAM_SORT);

  const category = rawCategory !== null && rawCategory !== "" ? rawCategory : undefined;
  const sort: SortValue = rawSort !== null && isValidSort(rawSort) ? rawSort : DEFAULT_SORT;

  return { category, sort };
}

/**
 * Produce URLSearchParams from a BoardFilter, preserving limit/offset from the
 * existing params so callers need only pass what they want to change.
 *
 * `category` is omitted from the output when undefined (no filter param in URL).
 */
export function filterToParams(
  filter: BoardFilter,
  existing?: URLSearchParams,
): URLSearchParams {
  const next = new URLSearchParams(existing);

  if (filter.category !== undefined && filter.category !== "") {
    next.set(PARAM_CATEGORY, filter.category);
  } else {
    next.delete(PARAM_CATEGORY);
  }

  if (filter.sort === DEFAULT_SORT) {
    next.delete(PARAM_SORT);
  } else {
    next.set(PARAM_SORT, filter.sort);
  }

  return next;
}

/**
 * Build the gateway query string for an initial board fetch (offset=0).
 * Includes limit + sort + optional category.
 */
export function buildGatewayParams(filter: BoardFilter, limit: number): string {
  const params = new URLSearchParams({ sort: filter.sort, limit: String(limit), offset: "0" });
  if (filter.category !== undefined) {
    params.set(PARAM_CATEGORY, filter.category);
  }
  return params.toString();
}

/**
 * Build the gateway query string for a paginated "load more" call.
 * Preserves the active filter+sort so pagination stays within the filtered set.
 */
export function buildPaginationParams(
  filter: BoardFilter,
  limit: number,
  offset: number,
): string {
  const params = new URLSearchParams({
    sort: filter.sort,
    limit: String(limit),
    offset: String(offset),
  });
  if (filter.category !== undefined) {
    params.set(PARAM_CATEGORY, filter.category);
  }
  return params.toString();
}
