"use client";
/**
 * SidebarNav — the navigation popover + account block at the top/bottom of the
 * global sidebar (#245).
 *
 * The brand button toggles a popover that holds the navigation links — they are
 * not shown permanently. Dashboard appears only for authors. The account block
 * shows signup/login when anonymous, email + logout when connected.
 *
 * Routes use the clarified labels: Archiviste (/), Lacunes (/lacunes),
 * État & métriques (/metriques), Dashboard (/dashboard, author-only).
 *
 * A09: email rendered as text only; never logged.
 */

import { useState } from "react";
import Link from "next/link";
import styles from "./AppShell.module.css";

export type Tier = "anonymous" | "member" | "author";

interface NavLink {
  href: string;
  label: string;
}

const NAV_LINKS: readonly NavLink[] = [
  { href: "/", label: "Archiviste" },
  { href: "/lacunes", label: "Lacunes" },
  { href: "/metriques", label: "État & métriques" },
];

export function NavPopover({ tier }: { tier: Tier }) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className={styles.brandBlock}>
      <button
        type="button"
        className={styles.brandButton}
        onClick={() => setIsOpen((open) => !open)}
        aria-expanded={isOpen}
        aria-haspopup="menu"
      >
        Archiviste Nocilia
      </button>
      {isOpen && (
        <div className={styles.popover} role="menu" aria-label="Navigation">
          {NAV_LINKS.map((link) => (
            <Link key={link.href} href={link.href} className={styles.popoverLink}>
              {link.label}
            </Link>
          ))}
          {tier === "author" && (
            <Link href="/dashboard" className={styles.popoverLink}>
              Dashboard
            </Link>
          )}
        </div>
      )}
    </div>
  );
}

export function AccountBlock({
  tier,
  email = null,
}: {
  tier: Tier;
  email?: string | null;
}) {
  if (tier === "anonymous") {
    return (
      <div className={styles.accountBlock}>
        <Link href="/signup" className={styles.accountLink}>
          S&apos;inscrire
        </Link>
        <Link href="/login" className={styles.accountLinkPrimary}>
          Se connecter
        </Link>
      </div>
    );
  }

  return (
    <div className={styles.accountBlock}>
      {email != null && email !== "" && (
        <span className={styles.userEmail}>{email}</span>
      )}
      <Link href="/logout" className={styles.accountLink}>
        Se déconnecter
      </Link>
    </div>
  );
}
