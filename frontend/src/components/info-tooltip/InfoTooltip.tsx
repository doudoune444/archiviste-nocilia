"use client";

/**
 * InfoTooltip — reusable information-tooltip leaf component (issue #251).
 *
 * No external dependency (no Radix / floating-ui). The trigger is a focusable,
 * tappable <button> carrying an info icon, an aria-label, and — while open —
 * an aria-describedby pointing at the tooltip content.
 *
 * Opens on click/tap, on mouse hover (desktop) and on keyboard focus.
 * Closes on Escape and on outside-click. Placement is a fixed top-centred
 * position; edge-clipping on small screens is a later CSS concern.
 */

import { useEffect, useId, useRef, useState } from "react";
import styles from "./InfoTooltip.module.css";

interface InfoTooltipProps {
  label: string;
  content: string;
}

export function InfoTooltip({ label, content }: InfoTooltipProps) {
  const [isOpen, setIsOpen] = useState(false);
  const tooltipId = useId();
  const containerRef = useRef<HTMLSpanElement>(null);

  useOutsideClose(isOpen, containerRef, () => setIsOpen(false));

  function handleKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape" && isOpen) {
      setIsOpen(false);
    }
  }

  return (
    <span className={styles.container} ref={containerRef}>
      <button
        type="button"
        className={styles.trigger}
        aria-label={label}
        aria-describedby={isOpen ? tooltipId : undefined}
        onClick={() => setIsOpen((open) => !open)}
        onMouseEnter={() => setIsOpen(true)}
        onMouseLeave={() => setIsOpen(false)}
        onFocus={() => setIsOpen(true)}
        onBlur={() => setIsOpen(false)}
        onKeyDown={handleKeyDown}
      >
        <InfoIcon />
      </button>
      {isOpen && (
        <span id={tooltipId} role="tooltip" className={styles.tooltip}>
          {content}
        </span>
      )}
    </span>
  );
}

function useOutsideClose(
  isOpen: boolean,
  containerRef: React.RefObject<HTMLSpanElement | null>,
  close: () => void
) {
  useEffect(() => {
    if (!isOpen) {
      return;
    }
    function handlePointerDown(event: PointerEvent) {
      const container = containerRef.current;
      if (container && !container.contains(event.target as Node)) {
        close();
      }
    }
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [isOpen, containerRef, close]);
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
      <circle cx="8" cy="4.75" r="0.9" fill="currentColor" />
      <rect x="7.15" y="6.75" width="1.7" height="5" rx="0.85" fill="currentColor" />
    </svg>
  );
}
