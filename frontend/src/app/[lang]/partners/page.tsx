import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { isLocale, LOCALES } from "@/i18n/config";
import { getDictionary } from "@/i18n/dictionaries";
import { fmt } from "@/i18n/format";
import styles from "../page.module.css";

// Условия программы — те же, что в настройках сервера по умолчанию (PUBLISHER_REVENUE_SHARE,
// EARNINGS_HOLD_DAYS, PAYOUT_MIN_AMOUNT, REFERRAL_SHARE, REFERRAL_DAYS в .env). Поменяли там — поменяйте здесь
const TERMS = { share: "60%", days: 14, min: 20, refShare: "10%", refDays: 365 };

export async function generateMetadata({ params }: PageProps<"/[lang]/partners">): Promise<Metadata> {
  const { lang } = await params;
  if (!isLocale(lang)) return {};
  const t = await getDictionary(lang);
  return {
    title: t.partners.metaTitle,
    description: fmt(t.partners.metaDescription, TERMS),
    alternates: { languages: Object.fromEntries(LOCALES.map((l) => [l, `/${l}/partners`])) },
  };
}

export default async function PartnersPage({ params }: PageProps<"/[lang]/partners">) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  const t = (await getDictionary(lang)).partners;
  // Пример; настоящий код с адресом платформы и кодом площадки партнёр копирует в кабинете
  const snippet = '<script async src="https://ads.example.com/widget.js" data-placement="s1_4f2a9c"></script>';

  return (
    <>
      <section className={styles.hero}>
        <div className={styles.glow} aria-hidden="true" />
        <div className={`container ${styles.heroInner}`}>
          <span className={styles.badge}>
            <span className={styles.badgeDot} />
            {t.badge}
          </span>
          <h1 className={styles.title}>
            {t.titleStart} <span className={styles.gradientText}>{fmt(t.titleAccent, TERMS)}</span>
          </h1>
          <p className={styles.lead}>{fmt(t.lead, TERMS)}</p>
          <div className={styles.actions}>
            <a href="/app#/partner" className="btn primary">{t.cta}</a>
            <a href="#referral" className="btn">{t.ctaReferral}</a>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className={styles.sectionHead}><h2>{t.howTitle}</h2></div>
          <ol className={styles.steps}>
            {t.steps.map((s, i) => (
              <li key={s.title} className="card">
                <span className={styles.stepNo}>{i + 1}</span>
                <h3>{s.title}</h3>
                <p className="muted">{fmt(s.text, TERMS)}</p>
              </li>
            ))}
          </ol>
          <div className="card" style={{ marginTop: 16 }}>
            <h3>{t.codeTitle}</h3>
            <p className="muted">{t.codeText}</p>
            <pre style={{ overflowX: "auto", padding: 12, borderRadius: 10, background: "var(--surface-2, var(--bg))",
              border: "1px solid var(--border)", fontSize: 13 }}><code>{snippet}</code></pre>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className={styles.sectionHead}><h2>{t.termsTitle}</h2></div>
          <div className={`grid ${styles.features}`}>
            {t.terms.map((f) => (
              <div key={f.title} className="card">
                <h3>{fmt(f.title, TERMS)}</h3>
                <p className="muted">{fmt(f.text, TERMS)}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="referral" className="section">
        <div className="container" style={{ maxWidth: 760, textAlign: "center" }}>
          <h2>{t.referralTitle}</h2>
          <p className="muted">{fmt(t.referralText, TERMS)}</p>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className={styles.sectionHead}><h2>{t.faqTitle}</h2></div>
          <div className={styles.faq}>
            {t.faq.map((item) => (
              <details key={item.q} className="card">
                <summary>{item.q}</summary>
                <p className="muted">{fmt(item.a, TERMS)}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      <section className="section center">
        <div className="container">
          <h2>{t.finalTitle}</h2>
          <a href="/app#/partner" className="btn primary">{t.cta}</a>
        </div>
      </section>
    </>
  );
}
