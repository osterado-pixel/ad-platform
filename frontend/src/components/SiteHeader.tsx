import Link from "next/link";

import type { Locale } from "@/i18n/config";
import type { Dictionary } from "@/i18n/dictionaries";
import { LanguageSwitcher } from "./LanguageSwitcher";
import styles from "./SiteHeader.module.css";

// Кабинет (/app) — отдельный интерфейс бэкенда на том же домене, поэтому обычные ссылки <a>, а не <Link>
export function SiteHeader({ lang, t }: { lang: Locale; t: Dictionary["header"] }) {
  return (
    <header className={styles.header}>
      <div className={`container ${styles.inner}`}>
        <Link href={`/${lang}`} className={styles.logo}>
          Ad Platform
        </Link>
        <nav className={styles.nav} aria-label={t.menu}>
          <Link href={`/${lang}#how`}>{t.how}</Link>
          <Link href={`/${lang}#pricing`}>{t.pricing}</Link>
          <Link href={`/${lang}/generate`}>{t.copywriter}</Link>
          <LanguageSwitcher current={lang} label={t.language} />
          <a href="/app" className="btn primary">
            {t.login}
          </a>
        </nav>
      </div>
    </header>
  );
}
