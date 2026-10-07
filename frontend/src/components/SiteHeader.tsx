import Link from "next/link";

import styles from "./SiteHeader.module.css";

// Кабинет (/app) — отдельный интерфейс бэкенда на том же домене, поэтому обычные ссылки <a>, а не <Link>
export function SiteHeader() {
  return (
    <header className={styles.header}>
      <div className={`container ${styles.inner}`}>
        <Link href="/" className={styles.logo}>
          Ad Platform
        </Link>
        <nav className={styles.nav} aria-label="Основное меню">
          <Link href="/#how">Как это работает</Link>
          <Link href="/#pricing">Цены</Link>
          <Link href="/generate">AI-копирайтер</Link>
          <a href="/app" className="btn primary">
            Войти
          </a>
        </nav>
      </div>
    </header>
  );
}
