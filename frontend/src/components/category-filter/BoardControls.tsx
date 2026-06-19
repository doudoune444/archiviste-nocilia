"use client";
/**
 * BoardControls — client component for category filter + sort selection (BOARD-003).
 *
 * AC: selecting a category filters the board; sort=priority|date reorders it.
 * AC: active filter+sort are reflected in URL search params (shareable).
 *
 * Drives the RSC re-fetch by pushing new search params via Next.js router.
 * Does NOT manage ticket state — that is owned by the RSC + LoadMoreButton.
 */

import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { filterFromParams, filterToParams } from "./params";
import type { SortValue } from "./params";
import styles from "./BoardControls.module.css";

interface BoardControlsProps {
  /** Ordered list of category strings present in the current board data. */
  availableCategories: string[];
}

export function BoardControls({ availableCategories }: BoardControlsProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const filter = filterFromParams(searchParams);

  function pushFilter(next: { category?: string | undefined; sort?: SortValue }) {
    const updated = filterToParams(
      {
        category: next.category !== undefined ? next.category : filter.category,
        sort: next.sort ?? filter.sort,
      },
      searchParams,
    );
    router.push(`${pathname}?${updated.toString()}`);
  }

  function handleCategoryChange(event: React.ChangeEvent<HTMLSelectElement>) {
    pushFilter({ category: event.target.value, sort: filter.sort });
  }

  function handleSortChange(event: React.ChangeEvent<HTMLSelectElement>) {
    const value = event.target.value as SortValue;
    pushFilter({ category: filter.category, sort: value });
  }

  function handleClearCategory() {
    pushFilter({ category: "", sort: filter.sort });
  }

  return (
    <div className={styles.controls} role="group" aria-label="Filtres et tri">
      <div className={styles.group}>
        <label htmlFor="board-category" className={styles.label}>
          Catégorie
        </label>
        <select
          id="board-category"
          className={styles.select}
          value={filter.category ?? ""}
          onChange={handleCategoryChange}
          data-testid="category-select"
        >
          <option value="">Toutes</option>
          {availableCategories.map((cat) => (
            <option key={cat} value={cat}>
              {cat}
            </option>
          ))}
        </select>
        {filter.category !== undefined && (
          <button
            className={styles.clearBtn}
            onClick={handleClearCategory}
            aria-label="Effacer le filtre catégorie"
            data-testid="clear-category"
          >
            ×
          </button>
        )}
      </div>

      <div className={styles.group}>
        <label htmlFor="board-sort" className={styles.label}>
          Tri
        </label>
        <select
          id="board-sort"
          className={styles.select}
          value={filter.sort}
          onChange={handleSortChange}
          data-testid="sort-select"
        >
          <option value="priority">Priorité</option>
          <option value="date">Date</option>
        </select>
      </div>
    </div>
  );
}
