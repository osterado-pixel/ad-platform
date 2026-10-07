"use client";

import { usePathname } from "next/navigation";

import { LOCALE_COOKIE, LOCALE_NAMES, LOCALES, type Locale } from "@/i18n/config";
import styles from "./SiteHeader.module.css";

// Выбор запоминается на год: адрес без языка (/) потом открывается на нём
function rememberLocale(locale: Locale) {
  document.cookie = `${LOCALE_COOKIE}=${locale}; path=/; max-age=31536000; samesite=lax`;
}

// Переключатель языка: та же страница на другом языке
export function LanguageSwitcher({ current, label }: { current: Locale; label: string }) {
  const pathname = usePathname();
  const rest = pathname.split("/").slice(2).join("/");
  return (
    <nav className={styles.langs} aria-label={label}>
      {LOCALES.map((locale) => (
        <a
          key={locale}
          href={`/${locale}${rest ? `/${rest}` : ""}`}
          hrefLang={locale}
          lang={locale}
          title={LOCALE_NAMES[locale]}
          aria-current={locale === current ? "true" : undefined}
          className={locale === current ? styles.langOn : styles.lang}
          onClick={() => rememberLocale(locale)}
        >
          {locale.toUpperCase()}
        </a>
      ))}
    </nav>
  );
}
