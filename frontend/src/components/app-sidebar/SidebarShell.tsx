"use client";
/**
 * SidebarShell — persistent left sidebar app-shell (#248).
 *
 * Replaces the former top navigation bar + global footer. Rendered on every
 * page from the root layout. Vertical structure, top to bottom:
 *   1. Brand button → opens a popover with the navigation links (Dashboard
 *      only for the author tier).
 *   2. "Nouvelle conversation" → resets the thread on the chat page, otherwise
 *      navigates to "/".
 *   3. Conversation history → only when the chat page registers it.
 *   4. Account block → email + logout when connected, signup/login otherwise.
 *
 * Responsive: fixed on desktop; on mobile (<600px) hidden and opened as an
 * overlay drawer via a hamburger button (CSS-driven; the open state toggles a
 * class so both layouts share one DOM tree).
 *
 * Identity is fetched server-side and forwarded as a prop — never client-supplied
 * (A01). Email is rendered as text only (A09).
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import styles from "./SidebarShell.module.css";
import type { Identity } from "./identity";
import { useChatSidebar } from "./SidebarChatContext";

const BRAND_LABEL = "L'Archiviste";
const BRAND_ICON = "🪶";
const BRAND_CHEVRON = "▾";
const NEW_CONVERSATION_LABEL = "Nouvelle conversation";

interface NavLink {
  href: string;
  label: string;
  icon: string;
}

const PRIMARY_NAV_LINKS: readonly NavLink[] = [
  { href: "/", label: "Archiviste", icon: "🪶" },
  { href: "/lacunes", label: "Lacunes", icon: "🔖" },
  { href: "/metriques", label: "État & métriques", icon: "📊" },
];

interface PopoverEntryProps {
  href: string;
  label: string;
  icon: string | null;
  onNavigate: () => void;
}

function PopoverEntry({ href, label, icon, onNavigate }: PopoverEntryProps) {
  return (
    <Link href={href} className={styles.popoverLink} onClick={onNavigate}>
      {icon !== null && (
        <span className={styles.popoverIcon} data-icon={icon} aria-hidden>
          {icon}
        </span>
      )}
      {label}
    </Link>
  );
}

interface BrandPopoverProps {
  identity: Identity;
  onNavigate: () => void;
}

function BrandPopover({ identity, onNavigate }: BrandPopoverProps) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className={styles.brandSection}>
      <button
        type="button"
        className={styles.brandButton}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        onClick={() => setIsOpen((open) => !open)}
      >
        <span className={styles.brandTile} data-icon={BRAND_ICON} aria-hidden>
          {BRAND_ICON}
        </span>
        <span className={styles.brandName}>{BRAND_LABEL}</span>
        <span className={styles.brandChevron} aria-hidden>
          {BRAND_CHEVRON}
        </span>
      </button>

      {isOpen && (
        <nav className={styles.popover} aria-label="Navigation">
          {PRIMARY_NAV_LINKS.map((link) => (
            <PopoverEntry
              key={link.href}
              href={link.href}
              label={link.label}
              icon={link.icon}
              onNavigate={onNavigate}
            />
          ))}
          {identity.tier === "author" && (
            <PopoverEntry
              href="/dashboard"
              label="Dashboard"
              icon={null}
              onNavigate={onNavigate}
            />
          )}
        </nav>
      )}
    </div>
  );
}

interface AccountBlockProps {
  identity: Identity;
}

function AccountBlock({ identity }: AccountBlockProps) {
  if (identity.tier === "anonymous") {
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
      {identity.email !== null && (
        <span className={styles.userEmail}>{identity.email}</span>
      )}
      <Link href="/logout" className={styles.accountLink}>
        Se déconnecter
      </Link>
    </div>
  );
}

interface SidebarShellProps {
  identity: Identity;
}

export function SidebarShell({ identity }: SidebarShellProps) {
  const router = useRouter();
  const chat = useChatSidebar();
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  const closeDrawer = useCallback(() => setIsDrawerOpen(false), []);

  const handleNewConversation = useCallback(() => {
    closeDrawer();
    if (chat !== null) {
      chat.onNewConversation();
      return;
    }
    router.push("/");
  }, [chat, router, closeDrawer]);

  return (
    <>
      <button
        type="button"
        className={styles.hamburger}
        aria-label="Ouvrir le menu"
        onClick={() => setIsDrawerOpen(true)}
      >
        ☰
      </button>

      {isDrawerOpen && (
        <div
          className={styles.backdrop}
          aria-hidden="true"
          onClick={closeDrawer}
        />
      )}

      <aside
        className={`${styles.sidebar} ${isDrawerOpen ? styles.sidebarOpen : ""}`}
        aria-label="Barre latérale"
      >
        <button
          type="button"
          className={styles.drawerClose}
          aria-label="Fermer le menu"
          onClick={closeDrawer}
        >
          ✕
        </button>

        <BrandPopover identity={identity} onNavigate={closeDrawer} />

        <button
          type="button"
          className={styles.newConversationButton}
          onClick={handleNewConversation}
          data-testid="new-conversation-btn"
        >
          {NEW_CONVERSATION_LABEL}
        </button>

        {chat !== null && (
          <div className={styles.historySlot}>{chat.history}</div>
        )}

        <div className={styles.spacer} />

        <AccountBlock identity={identity} />
      </aside>
    </>
  );
}
