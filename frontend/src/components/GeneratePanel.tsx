"use client";

import { useEffect, useState } from "react";

import { useAdTask } from "@/hooks/useAdTask";
import { api, ApiError, getToken, type CopywriterStatus, type Me } from "@/services/api";

type Access =
  | { kind: "loading" }
  | { kind: "guest" }
  | { kind: "disabled" }
  | { kind: "error"; message: string }
  | { kind: "ready"; me: Me; copywriter: CopywriterStatus };

const money = (v: number) => v.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function GeneratePanel() {
  const [access, setAccess] = useState<Access>({ kind: "loading" });
  const [product, setProduct] = useState("");
  const [audience, setAudience] = useState("");
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

  if (access.kind === "loading") return <p className="muted">Загрузка…</p>;
  if (access.kind === "guest") {
    return (
      <div className="card">
        <p>Чтобы составить объявление, войдите в кабинет или зарегистрируйтесь — это бесплатно.</p>
        <a href="/app" className="btn primary">Войти</a>
        <p className="muted" style={{ marginTop: 12, fontSize: 14 }}>
          После входа вернитесь на эту страницу — вход общий.
        </p>
      </div>
    );
  }
  if (access.kind === "disabled") {
    return <div className="notice info">AI-копирайтер сейчас выключен администратором платформы.</div>;
  }
  if (access.kind === "error") {
    return <div className="notice error">Не удалось загрузить данные: {access.message}</div>;
  }

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
          Баланс: <b>{money(me.balance)}</b>
          {me.held_balance > 0 && <> · заморожено {money(me.held_balance)}</>}. На время генерации заморозится{" "}
          {money(copywriter.hold_amount)}, спишется по факту (обычно меньше), остальное вернётся.
        </div>
        <div>
          <label htmlFor="product">Описание товара</label>
          <textarea
            id="product" rows={4} maxLength={2000} value={product}
            placeholder="Что рекламируете: товар или услуга, чем хороши, цена, особенности"
            onChange={(e) => setProduct(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="audience">Целевая аудитория (необязательно)</label>
          <input
            id="audience" maxLength={300} value={audience} placeholder="Общая аудитория"
            onChange={(e) => setAudience(e.target.value)}
          />
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <button
            type="button" className="btn primary" disabled={task.isRunning || tooShort}
            onClick={() => task.start(product.trim(), audience.trim() || "Общая аудитория")}
          >
            Сгенерировать варианты
          </button>
          {tooShort && product.length > 0 && <span className="muted" style={{ fontSize: 14 }}>Опишите подробнее — от 10 символов</span>}
        </div>
        {task.isRunning && (
          <div>
            <progress />
            <div className="muted" style={{ fontSize: 14 }}>
              {task.status === "pending" ? "Ожидание в очереди…" : "Генерируем…"} {task.elapsed} с
            </div>
          </div>
        )}
        {(task.status === "failed" || task.status === "timeout") && <div className="notice error">{task.error}</div>}
      </div>

      {task.status === "completed" && task.variants && (
        <>
          <div className="notice ok">Готово — скопируйте понравившийся вариант в форму кампании в кабинете.</div>
          {task.variants.map((v, i) => {
            const text = `${v.title}\n${v.text} ${v.cta}`;
            return (
              <div key={`${i}-${v.title}`} className="card ai-variant" style={{ display: "grid", gap: 6 }}>
                <div className="muted" style={{ fontSize: 14 }}>Вариант {i + 1}</div>
                <b>{v.title}</b>
                <div>{v.text}</div>
                <div className="muted" style={{ fontSize: 14 }}>Призыв к действию: {v.cta}</div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button type="button" className="btn" onClick={() => copy(text, String(i))}>
                    {copied === String(i) ? "Скопировано" : "Скопировать"}
                  </button>
                  <a className="btn" href="/app#/campaigns/new">Создать кампанию</a>
                </div>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}
