import type { Metadata } from "next";
import "@/styles/globals.css";
import { AppShellServer } from "@/components/app-shell/AppShellServer";

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
        {/* #245: global Mistral-style app-shell — fixed left sidebar on every
            page, no top nav bar, no global footer. */}
        <AppShellServer>{children}</AppShellServer>
      </body>
    </html>
  );
}
