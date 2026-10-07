"use client";

import { useEffect, useState, type FormEvent } from "react";

import { useAdTask } from "@/hooks/useAdTask";
import { LOCALE_NAMES, LOCALES, type Locale } from "@/i18n/config";
import type { Dictionary } from "@/i18n/dictionaries";
import { fmt, money } from "@/i18n/format";
import { api, ApiError, getToken, type CopywriterStatus } from "@/services/api";
import styles from "./LiveDemo.module.css";

type Access = "checking" | "guest" | "ready" | "disabled";

export function LiveDemo({ lang, t }: { lang: Locale; t: Dictionary["copy"] }) {
  const [access, setAccess] = useState<Access>("checking");
  const [holdAmount, setHoldAmount] = useState<number | null>(null);
  const [product, setProduct] = useState("");
  const [audience, setAudience] = useState("");
  const [adLanguage, setAdLanguage] = useState<Locale>(lang);  // язык объявлений — по умолчанию язык сайта
  const [variant, setVariant] = useState(0);
  const task = useAdTask();

  // Вход — общий с кабинетом /app (тот же домен, токен в localStorage)
  useEffect(() => {
    if (!getToken()) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- проверка входа при открытии страницы
      setAccess("guest");
      return;
    }
    api<CopywriterStatus>("/ai/status")
      .then((s) => { setAccess(s.enabled ? "ready" : "disabled"); setHoldAmount(s.hold_amount); })
      .catch((err) => setAccess(err instanceof ApiError && err.status === 401 ? "guest" : "disabled"));
  }, []);

  const tooShort = product.trim().length < 10;
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (access !== "ready" || tooShort || task.isRunning) return;
    setVariant(0);
    task.start(product.trim(), audience.trim() || t.defaultAudience, adLanguage);
  };

  const showing = task.status === "completed" && task.variants ? task.variants : t.sample;
  const isSample = showing === t.sample;
  const current = showing[Math.min(variant, showing.length - 1)];

  return (
    <div className={styles.demo}>
      <form onSubmit={onSubmit} className={styles.form}>
        <div>
          <label htmlFor="demo-product">{t.productLabel}</label>
          <textarea
            id="demo-product" rows={4} maxLength={2000} value={product}
            onChange={(e) => setProduct(e.target.value)} placeholder={t.productPlaceholder}
          />
        </div>
        <div className={styles.row2}>
          <div>
            <label htmlFor="demo-audience">{t.audienceLabel}</label>
            <input
              id="demo-audience" maxLength={300} value={audience}
              onChange={(e) => setAudience(e.target.value)} placeholder={t.audiencePlaceholder}
            />
          </div>
          <div>
            <label htmlFor="demo-ad-language">{t.adLanguage}</label>
            <select id="demo-ad-language" value={adLanguage} onChange={(e) => setAdLanguage(e.target.value as Locale)}>
              {LOCALES.map((l) => <option key={l} value={l}>{LOCALE_NAMES[l]}</option>)}
            </select>
          </div>
        </div>

        {access === "ready" && (
          <button type="submit" className={`btn primary ${styles.submit}`} disabled={task.isRunning || tooShort}>
            {task.isRunning ? (
              <><span className={styles.spinner} aria-hidden="true" /> {t.status[task.status]}… {task.elapsed} {t.seconds}</>
            ) : t.generate}
          </button>
        )}
        {access === "ready" && holdAmount !== null && (
          <p className={styles.note}>{fmt(t.holdNote, { amount: money(holdAmount, lang) })}</p>
        )}
        {access === "guest" && (
          <div className={styles.gate}>
            <p>{t.guestText}</p>
            <a href="/app" className="btn primary">{t.guestCta}</a>
          </div>
        )}
        {access === "disabled" && <div className="notice info">{t.disabled}</div>}
      </form>

      <div className={styles.panel} aria-live="polite">
        <div className={styles.panelHead}>
          <span className={styles.status}>
            <span className={`${styles.dot} ${task.isRunning ? styles.dotBusy : ""}`} />
            {isSample ? t.sampleLabel : t.status[task.status]}
          </span>
          <span className={styles.muted}>{t.variantsCount}</span>
        </div>

        {(task.status === "failed" || task.status === "timeout") && (
          <div className="notice error">{task.error ?? (task.status === "timeout" ? t.timeout : t.failedFallback)}</div>
        )}

        <div className={styles.tabs} role="tablist" aria-label={t.variantsLabel}>
          {showing.map((v, i) => (
            <button
              key={`${i}-${v.title}`} type="button" role="tab" aria-selected={i === variant}
              className={i === variant ? styles.tabOn : styles.tab} onClick={() => setVariant(i)}
            >
              {fmt(t.variant, { n: i + 1 })}
            </button>
          ))}
        </div>

        <dl className={`${styles.fields} ai-variant`}>
          <dt>{t.fieldTitle}</dt>
          <dd className={styles.headline}>{current.title}</dd>
          <dt>{t.fieldText}</dt>
          <dd>{current.text}</dd>
          <dt>{t.fieldCta}</dt>
          <dd className={styles.cta}>{current.cta}</dd>
        </dl>

        <p className={styles.footnote}>{isSample ? t.footnoteSample : t.footnoteReal}</p>
      </div>
    </div>
  );
}
