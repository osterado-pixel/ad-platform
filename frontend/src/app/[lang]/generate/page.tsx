import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { GeneratePanel } from "@/components/GeneratePanel";
import { isLocale, LOCALES } from "@/i18n/config";
import { getDictionary } from "@/i18n/dictionaries";

export async function generateMetadata({ params }: PageProps<"/[lang]/generate">): Promise<Metadata> {
  const { lang } = await params;
  if (!isLocale(lang)) return {};
  const t = await getDictionary(lang);
  return {
    title: t.generatePage.metaTitle,
    description: t.generatePage.metaDescription,
    alternates: { languages: Object.fromEntries(LOCALES.map((l) => [l, `/${l}/generate`])) },
  };
}

export default async function GeneratePage({ params }: PageProps<"/[lang]/generate">) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  const t = await getDictionary(lang);
  return (
    <section className="section">
      <div className="container" style={{ maxWidth: 760 }}>
        <h1>{t.generatePage.title}</h1>
        <p className="muted">{t.generatePage.lead}</p>
        <GeneratePanel lang={lang} t={t.copy} />
      </div>
    </section>
  );
}
