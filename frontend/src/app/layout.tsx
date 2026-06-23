import type { Metadata } from "next";
import "@/styles/globals.css";
import styles from "./layout.module.css";
import AppSidebar from "@/components/app-sidebar/AppSidebar";
import { SidebarChatProvider } from "@/components/app-sidebar/SidebarChatContext";

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
        <SidebarChatProvider>
          <div className={styles.shell}>
            <AppSidebar />
            <main className={styles.main}>{children}</main>
          </div>
        </SidebarChatProvider>
      </body>
    </html>
  );
}
