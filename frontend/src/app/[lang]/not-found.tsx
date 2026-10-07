import Link from "next/link";
import { lang as rootLang } from "next/root-params";

import { DEFAULT_LOCALE, isLocale } from "@/i18n/config";
import { getDictionary } from "@/i18n/dictionaries";

// «Страница не найдена» на языке адреса (/de/… → по-немецки)
export default async function NotFound() {
  const value = await rootLang();
  const locale = isLocale(value) ? value : DEFAULT_LOCALE;
  const t = (await getDictionary(locale)).notFound;
  return (
    <section className="section center">
      <div className="container">
        <h1>{t.title}</h1>
        <p className="muted">{t.text}</p>
        <Link href={`/${locale}`} className="btn primary">{t.home}</Link>
      </div>
    </section>
  );
}
