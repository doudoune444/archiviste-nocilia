"use client";

/**
 * InfoTooltip — reusable information tooltip leaf component (issue #251).
 *
 * The trigger is a focusable, tappable <button> carrying an info icon, with an
 * `aria-label` and (when open) an `aria-describedby` pointing at the tooltip
 * content. The tooltip opens on click/tap, on mouse hover and on keyboard
 * focus; it closes on Escape and on outside-click.
 *
 * No external dependency (no Radix / floating-ui). Placement is a fixed
 * top-centred position; edge-clipping on small screens is a later CSS concern.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import styles from "./InfoTooltip.module.css";

interface InfoTooltipProps {
  label: string;
  content: string;
}

export function InfoTooltip({ label, content }: InfoTooltipProps) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLSpanElement>(null);
  const tooltipId = useId();

  const open = useCallback(() => setIsOpen(true), []);
  const close = useCallback(() => setIsOpen(false), []);
  const toggle = useCallback(() => setIsOpen((previous) => !previous), []);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        close();
      }
    }

    function handleOutsidePointer(event: MouseEvent) {
      const target = event.target as Node;
      if (containerRef.current && !containerRef.current.contains(target)) {
        close();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("mousedown", handleOutsidePointer);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("mousedown", handleOutsidePointer);
    };
  }, [isOpen, close]);

  return (
    <span
      ref={containerRef}
      className={styles.container}
      onMouseEnter={open}
      onMouseLeave={close}
    >
      <button
        type="button"
        className={styles.trigger}
        aria-label={label}
        aria-describedby={isOpen ? tooltipId : undefined}
        aria-expanded={isOpen}
        onClick={toggle}
        onFocus={open}
        onBlur={close}
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

function InfoIcon() {
  return (
    <svg
      className={styles.icon}
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5" />
      <line
        x1="8"
        y1="7"
        x2="8"
        y2="11.5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <circle cx="8" cy="4.5" r="0.9" fill="currentColor" />
    </svg>
  );
}
