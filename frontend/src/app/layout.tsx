import type { Metadata } from "next";
import Link from "next/link";
import "@/styles/globals.css";
import styles from "./layout.module.css";

export const metadata: Metadata = {
  title: "Archiviste Nocilia",
  description:
    "Posez vos questions sur le lore de Nocilia. Réponses sourcées depuis les archives.",
};

interface RootLayoutProps {
  children: React.ReactNode;
}

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html lang="fr">
      <body>
        <div className={styles.shell}>
          <nav className={styles.nav}>
            <Link href="/" className={styles.navBrand}>
              Archiviste Nocilia
            </Link>
            <ul className={styles.navLinks}>
              <li>
                <Link href="/" className={styles.navLink}>
                  Accueil
                </Link>
              </li>
              <li>
                <Link href="/board" className={styles.navLink}>
                  Tickets
                </Link>
              </li>
            </ul>
          </nav>
          <main className={styles.main}>{children}</main>
          <footer className={styles.footer}>
            <p>Archives de Nocilia — usage personnel</p>
          </footer>
        </div>
      </body>
    </html>
  );
}
