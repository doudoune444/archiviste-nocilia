import Link from "next/link";
import styles from "./page.module.css";

export default function AccueilPage() {
  return (
    <section className={styles.hero}>
      <h1 className={styles.heading}>Bienvenue aux archives de Nocilia</h1>
      <p className={styles.lead}>
        Posez vos questions sur le lore du monde de Nocilia. L&apos;archiviste
        vous répond en citant ses sources directement depuis les archives.
      </p>
      <Link href="/chat" className={styles.cta}>
        Interroger l&apos;archiviste
      </Link>
    </section>
  );
}
