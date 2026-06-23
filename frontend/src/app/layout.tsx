import type { Metadata } from "next";
import "@/styles/globals.css";
import styles from "./layout.module.css";
import AuthAwareNav from "@/components/auth-aware-nav/AuthAwareNav";

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
          <AuthAwareNav />
          <main className={styles.main}>{children}</main>
        </div>
      </body>
    </html>
  );
}
