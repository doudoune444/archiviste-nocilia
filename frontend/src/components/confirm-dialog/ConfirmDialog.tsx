"use client";

import { useEffect, useId, useRef } from "react";
import styles from "./ConfirmDialog.module.css";

interface ConfirmDialogProps {
  title: string;
  message: string;
  dangerLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}

const FOCUSABLE_SELECTOR =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

export function ConfirmDialog({
  title,
  message,
  dangerLabel,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const titleId = useId();
  const messageId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    cancelButtonRef.current?.focus();
  }, []);

  function handleKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      onCancel();
      return;
    }
    if (event.key === "Tab") {
      trapFocus(event, dialogRef.current);
    }
  }

  function handleOverlayClick(event: React.MouseEvent) {
    if (event.target === event.currentTarget) {
      onCancel();
    }
  }

  return (
    <div className={styles.overlay} onClick={handleOverlayClick}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={messageId}
        className={styles.dialog}
        onKeyDown={handleKeyDown}
      >
        <h2 id={titleId} className={styles.title}>
          {title}
        </h2>
        <p id={messageId} className={styles.message}>
          {message}
        </p>
        <div className={styles.actions}>
          <button
            ref={cancelButtonRef}
            type="button"
            className={styles.cancel}
            onClick={onCancel}
          >
            Annuler
          </button>
          <button type="button" className={styles.danger} onClick={onConfirm}>
            {dangerLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

function trapFocus(event: React.KeyboardEvent, dialog: HTMLElement | null) {
  if (!dialog) {
    return;
  }
  const focusable = Array.from(
    dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
  );
  if (focusable.length === 0) {
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const active = document.activeElement;

  if (event.shiftKey && active === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}
