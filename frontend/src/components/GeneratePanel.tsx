"use client";

import { useEffect, useState } from "react";

import { useAdTask } from "@/hooks/useAdTask";
import { LOCALE_NAMES, LOCALES, type Locale } from "@/i18n/config";
import type { Dictionary } from "@/i18n/dictionaries";
import { fmt, money } from "@/i18n/format";
import { api, ApiError, getToken, type CopywriterStatus, type Me } from "@/services/api";

type Access =
  | { kind: "loading" }
  | { kind: "guest" }
  | { kind: "disabled" }
  | { kind: "error"; message: string }
  | { kind: "ready"; me: Me; copywriter: CopywriterStatus };

export function GeneratePanel({ lang, t }: { lang: Locale; t: Dictionary["copy"] }) {
  const [access, setAccess] = useState<Access>({ kind: "loading" });
  const [product, setProduct] = useState("");
  const [audience, setAudience] = useState("");
  const [adLanguage, setAdLanguage] = useState<Locale>(lang);
  const [copied, setCopied] = useState<string | null>(null);
  const task = useAdTask();

  // Вход — общий с кабинетом /app (тот же домен, токен в localStorage)
  const loadAccess = async () => {
    if (!getToken()) { setAccess({ kind: "guest" }); return; }
    try {
      const [me, copywriter] = await Promise.all([api<Me>("/auth/me"), api<CopywriterStatus>("/ai/status")]);
      setAccess(copywriter.enabled ? { kind: "ready", me, copywriter } : { kind: "disabled" });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setAccess({ kind: "guest" }); // токен истёк
      else setAccess({ kind: "error", message: err instanceof Error ? err.message : String(err) });
    }
  };

  useEffect(() => {
    // Данные — только в браузере: токен лежит в localStorage, на сервере его нет
    // eslint-disable-next-line react-hooks/set-state-in-effect -- загрузка при открытии страницы
    loadAccess();
  }, []);

  // После генерации баланс изменился (списано по факту или возвращено) — обновляем
  useEffect(() => {
    if (task.status === "completed" || task.status === "failed") {
      api<Me>("/auth/me")
        .then((me) => setAccess((a) => (a.kind === "ready" ? { ...a, me } : a)))
        .catch(() => {});
    }
  }, [task.status]);

  if (access.kind === "loading") return <p className="muted">{t.loading}</p>;
  if (access.kind === "guest") {
    return (
      <div className="card">
        <p>{t.guestText}</p>
        <a href="/app" className="btn primary">{t.guestCta}</a>
        <p className="muted" style={{ marginTop: 12, fontSize: 14 }}>{t.guestHint}</p>
      </div>
    );
  }
  if (access.kind === "disabled") return <div className="notice info">{t.disabled}</div>;
  if (access.kind === "error") return <div className="notice error">{fmt(t.loadError, { error: access.message })}</div>;

  const { me, copywriter } = access;
  const tooShort = product.trim().length < 10;
  const copy = async (text: string, key: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(key);
    } catch {
      setCopied(null);
    }
  };

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div className="card" style={{ display: "grid", gap: 12 }}>
        <div className="muted" style={{ fontSize: 14 }}>
          {t.balance}: <b>{money(me.balance, lang)}</b>
          {me.held_balance > 0 && <> · {t.held} {money(me.held_balance, lang)}</>}.{" "}
          {fmt(t.holdNote, { amount: money(copywriter.hold_amount, lang) })}
        </div>
        <div>
          <label htmlFor="product">{t.productLabel}</label>
          <textarea
            id="product" rows={4} maxLength={2000} value={product} placeholder={t.productPlaceholder}
            onChange={(e) => setProduct(e.target.value)}
          />
        </div>
        <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
          <div>
            <label htmlFor="audience">{t.audienceLabel}</label>
            <input
              id="audience" maxLength={300} value={audience} placeholder={t.audiencePlaceholder}
              onChange={(e) => setAudience(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="ad-language">{t.adLanguage}</label>
            <select id="ad-language" value={adLanguage} onChange={(e) => setAdLanguage(e.target.value as Locale)}>
              {LOCALES.map((l) => <option key={l} value={l}>{LOCALE_NAMES[l]}</option>)}
            </select>
          </div>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <button
            type="button" className="btn primary" disabled={task.isRunning || tooShort}
            onClick={() => task.start(product.trim(), audience.trim() || t.defaultAudience, adLanguage)}
          >
            {t.generate}
          </button>
          {tooShort && product.length > 0 && <span className="muted" style={{ fontSize: 14 }}>{t.tooShort}</span>}
        </div>
        {task.isRunning && (
          <div>
            <progress />
            <div className="muted" style={{ fontSize: 14 }}>
              {t.status[task.status]}… {task.elapsed} {t.seconds}
            </div>
          </div>
        )}
        {(task.status === "failed" || task.status === "timeout") && (
          <div className="notice error">{task.error ?? (task.status === "timeout" ? t.timeout : t.failedFallback)}</div>
        )}
      </div>

      {task.status === "completed" && task.variants && (
        <>
          <div className="notice ok">{t.done}</div>
          {task.variants.map((v, i) => {
            const text = `${v.title}\n${v.text} ${v.cta}`;
            return (
              <div key={`${i}-${v.title}`} className="card ai-variant" style={{ display: "grid", gap: 6 }}>
                <div className="muted" style={{ fontSize: 14 }}>{fmt(t.variant, { n: i + 1 })}</div>
                <b>{v.title}</b>
                <div>{v.text}</div>
                <div className="muted" style={{ fontSize: 14 }}>{t.fieldCta}: {v.cta}</div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button type="button" className="btn" onClick={() => copy(text, String(i))}>
                    {copied === String(i) ? t.copied : t.copy}
                  </button>
                  <a className="btn" href="/app#/campaigns/new">{t.createCampaign}</a>
                </div>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}
