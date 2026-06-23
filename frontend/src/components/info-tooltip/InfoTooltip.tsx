"use client";

/**
 * InfoTooltip — reusable, dependency-free information-tooltip leaf component.
 *
 * Issue #251 (Observabilité, Lot 1, slice 1). Consumed in 5 places by the
 * Qualité RAG card (slice 2) and designed for reuse elsewhere.
 *
 * The trigger is a focusable, tappable <button> carrying an info icon, with an
 * `aria-label` and (when open) an `aria-describedby` pointing at the tooltip
 * content. The tooltip opens on click/tap, on mouse hover (desktop) and on
 * keyboard focus; it closes on Escape and on outside-click. Placement is a
 * fixed top-centred position — edge-clipping on small screens is a later CSS
 * concern, deliberately not solved with a positioning library here.
 *
 * No external dependency (no Radix / floating-ui), per the acceptance criteria.
 */

import { useEffect, useId, useRef, useState } from "react";
import styles from "./InfoTooltip.module.css";

interface InfoTooltipProps {
  /** Accessible label for the trigger button (e.g. "Qu'est-ce que la fidélité ?"). */
  label: string;
  /** Tooltip body text revealed when the tooltip is open. */
  content: string;
}

function InfoIcon() {
  return (
    <svg
      className={styles.icon}
      viewBox="0 0 16 16"
      width="16"
      height="16"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="8" cy="4.5" r="1" fill="currentColor" />
      <rect x="7.25" y="7" width="1.5" height="5" rx="0.75" fill="currentColor" />
    </svg>
  );
}

export function InfoTooltip({ label, content }: InfoTooltipProps) {
  const [isOpen, setIsOpen] = useState(false);
  const contentId = useId();
  const containerRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    function handleOutsidePointer(event: MouseEvent): void {
      if (!containerRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }

    document.addEventListener("mousedown", handleOutsidePointer);
    return () => {
      document.removeEventListener("mousedown", handleOutsidePointer);
    };
  }, [isOpen]);

  function handleKeyDown(event: React.KeyboardEvent<HTMLButtonElement>): void {
    if (event.key === "Escape") {
      setIsOpen(false);
    }
  }

  return (
    <span
      ref={containerRef}
      className={styles.container}
      onMouseEnter={() => setIsOpen(true)}
      onMouseLeave={() => setIsOpen(false)}
    >
      <button
        type="button"
        className={styles.trigger}
        aria-label={label}
        aria-expanded={isOpen}
        aria-describedby={isOpen ? contentId : undefined}
        onClick={() => setIsOpen((open) => !open)}
        onFocus={() => setIsOpen(true)}
        onBlur={() => setIsOpen(false)}
        onKeyDown={handleKeyDown}
      >
        <InfoIcon />
      </button>
      {isOpen && (
        <span id={contentId} role="tooltip" className={styles.content}>
          {content}
        </span>
      )}
    </span>
  );
}
