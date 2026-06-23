"use client";

/**
 * InfoTooltip — reusable info-icon leaf client (#246, Qualité RAG lisible).
 *
 * The trigger is a real <button> so it is focusable, tappable and announced by
 * screen readers. Its explanation is linked via aria-describedby.
 *
 * Opens on click/tap, mouse hover (desktop) and keyboard focus.
 * Closes on Escape and on a click outside.
 *
 * No external positioning library (project rule: a new dependency would need a
 * flag). Placement is fixed top-centered in CSS; edge clipping on small screens
 * is a later CSS concern, not a runtime one.
 */

import { useEffect, useId, useRef, useState } from "react";
import styles from "./InfoTooltip.module.css";

interface InfoTooltipProps {
  label: string;
  explanation: string;
}

export function InfoTooltip({ label, explanation }: InfoTooltipProps) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLSpanElement>(null);
  const tooltipId = useId();

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        setIsOpen(false);
      }
    }

    function handlePointerDown(event: MouseEvent): void {
      const container = containerRef.current;
      if (container && !container.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("mousedown", handlePointerDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("mousedown", handlePointerDown);
    };
  }, [isOpen]);

  return (
    <span ref={containerRef} className={styles.container}>
      <button
        type="button"
        className={styles.trigger}
        aria-label={label}
        aria-describedby={isOpen ? tooltipId : undefined}
        aria-expanded={isOpen}
        onClick={() => setIsOpen((open) => !open)}
        onMouseEnter={() => setIsOpen(true)}
        onMouseLeave={() => setIsOpen(false)}
        onFocus={() => setIsOpen(true)}
        onBlur={() => setIsOpen(false)}
      >
        <span aria-hidden="true">i</span>
      </button>
      {isOpen && (
        <span id={tooltipId} role="tooltip" className={styles.bubble}>
          {explanation}
        </span>
      )}
    </span>
  );
}
