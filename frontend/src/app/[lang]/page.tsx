import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { LiveDemo } from "@/components/LiveDemo";
import { isLocale } from "@/i18n/config";
import { getDictionary } from "@/i18n/dictionaries";
import styles from "./page.module.css";

export async function generateMetadata({ params }: PageProps<"/[lang]">): Promise<Metadata> {
  const { lang } = await params;
  if (!isLocale(lang)) return {};
  const t = await getDictionary(lang);
  return { title: { absolute: t.meta.title } };  // полный заголовок, без шаблона «%s — Ad Platform»
}

export default async function Home({ params }: PageProps<"/[lang]">) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  const dict = await getDictionary(lang);
  const t = dict.home;

  return (
    <>
      {/* 1. Первый экран */}
      <section className={styles.hero}>
        <div className={styles.glow} aria-hidden="true" />
        <div className={`container ${styles.heroInner}`}>
          <span className={styles.badge}>
            <span className={styles.badgeDot} />
            {t.badge}
          </span>
          <h1 className={styles.title}>
            {t.titleStart} <span className={styles.gradientText}>{t.titleAccent}</span>
          </h1>
          <p className={styles.lead}>{t.lead}</p>
          <div className={styles.actions}>
            <Link href="#demo" className="btn primary">{t.ctaTry}</Link>
            <Link href="#pricing" className="btn">{t.ctaPricing}</Link>
          </div>
        </div>
      </section>

      {/* 2. Живое демо */}
      <section id="demo" className="section">
        <div className="container">
          <div className={styles.sectionHead}>
            <h2>{t.demoTitle}</h2>
            <p className="muted">{t.demoLead}</p>
          </div>
          <LiveDemo lang={lang} t={dict.copy} />
        </div>
      </section>

      {/* 3. Как это работает */}
      <section id="how" className="section">
        <div className="container">
          <div className={styles.sectionHead}><h2>{t.howTitle}</h2></div>
          <ol className={styles.steps}>
            {t.steps.map((s, i) => (
              <li key={s.title} className="card">
                <span className={styles.stepNo}>{i + 1}</span>
                <h3>{s.title}</h3>
                <p className="muted">{s.text}</p>
              </li>
            ))}
          </ol>
          <div className={`grid ${styles.features}`}>
            {t.features.map((f) => (
              <div key={f.title} className="card">
                <h3>{f.title}</h3>
                <p className="muted">{f.text}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* 4. Цены (выделен средний тариф — AI-копирайтер) */}
      <section id="pricing" className="section">
        <div className="container">
          <div className={styles.sectionHead}>
            <h2>{t.pricingTitle}</h2>
            <p className="muted">{t.pricingLead}</p>
          </div>
          <div className={styles.plans}>
            {t.plans.map((p, i) => {
              const primary = i === 1;
              return (
                <div key={p.name} className={`${styles.plan} ${primary ? styles.planPrimary : ""}`}>
                  {primary && <span className={styles.popular}>{t.popular}</span>}
                  <h3>{p.name}</h3>
                  <p className="muted">{p.note}</p>
                  <div className={styles.price}>
                    {p.price} <span>{p.unit}</span>
                  </div>
                  <ul className={styles.planItems}>
                    {p.items.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                  <a href={primary ? "#demo" : "/app"} className={`btn ${primary ? "primary" : ""} ${styles.planCta}`}>
                    {p.cta}
                  </a>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* 5. Вопросы */}
      <section className="section">
        <div className="container">
          <div className={styles.sectionHead}><h2>{t.faqTitle}</h2></div>
          <div className={styles.faq}>
            {t.faq.map((item) => (
              <details key={item.q} className="card">
                <summary>{item.q}</summary>
                <p className="muted">{item.a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      <section className="section center">
        <div className="container">
          <h2>{t.finalTitle}</h2>
          <a href="/app" className="btn primary">{t.finalCta}</a>
        </div>
      </section>
    </>
  );
}
