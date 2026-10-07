"use client";

import { useEffect, useState, type FormEvent } from "react";

import { useAdTask } from "@/hooks/useAdTask";
import { api, ApiError, getToken, type AdVariant, type CopywriterStatus } from "@/services/api";
import styles from "./LiveDemo.module.css";

// Пример ответа — пока посетитель ничего не запустил (и для гостей: генерация платная, нужен вход)
const SAMPLE: AdVariant[] = [
  { title: "Python за 3 месяца", text: "40 уроков с практикой и наставником. Сертификат в конце", cta: "Начать учиться" },
  { title: "Первая программа — сегодня", text: "Курс для новичков: от основ до своего проекта", cta: "Записаться" },
  { title: "Профессия с нуля", text: "Практика с первого урока и разбор ваших задач", cta: "Попробовать" },
];

type Access = "checking" | "guest" | "ready" | "disabled";

const STATUS_LABEL: Record<string, string> = {
  idle: "ожидание",
  pending: "в очереди · средства заморожены",
  processing: "генерация",
  completed: "готово",
  failed: "ошибка · средства возвращены",
  timeout: "дольше обычного",
};

export function LiveDemo() {
  const [access, setAccess] = useState<Access>("checking");
  const [holdAmount, setHoldAmount] = useState<number | null>(null);
  const [product, setProduct] = useState("");
  const [audience, setAudience] = useState("");
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
    task.start(product.trim(), audience.trim() || "Общая аудитория");
  };

  const showing = task.status === "completed" && task.variants ? task.variants : SAMPLE;
  const isSample = showing === SAMPLE;
  const current = showing[Math.min(variant, showing.length - 1)];

  return (
    <div className={styles.demo}>
      <form onSubmit={onSubmit} className={styles.form}>
        <div>
          <label htmlFor="demo-product">Описание товара или услуги</label>
          <textarea
            id="demo-product" rows={4} maxLength={2000} value={product}
            onChange={(e) => setProduct(e.target.value)}
            placeholder="Например: онлайн-курс Python для начинающих — 40 уроков, практика, сертификат"
          />
        </div>
        <div>
          <label htmlFor="demo-audience">Целевая аудитория</label>
          <input
            id="demo-audience" maxLength={300} value={audience}
            onChange={(e) => setAudience(e.target.value)} placeholder="Студенты, начинающие разработчики"
          />
        </div>

        {access === "ready" && (
          <button type="submit" className={`btn primary ${styles.submit}`} disabled={task.isRunning || tooShort}>
            {task.isRunning ? (
              <><span className={styles.spinner} aria-hidden="true" /> {STATUS_LABEL[task.status]}… {task.elapsed} с</>
            ) : "Сгенерировать варианты"}
          </button>
        )}
        {access === "ready" && holdAmount !== null && (
          <p className={styles.note}>
            На время генерации заморозится до {holdAmount.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}, спишется по факту — обычно около цента.
            Не получилось — вернётся всё.
          </p>
        )}
        {access === "guest" && (
          <div className={styles.gate}>
            <p>Генерация списывается с баланса аккаунта — войдите или зарегистрируйтесь, это бесплатно.</p>
            <a href="/app" className="btn primary">Войти и попробовать</a>
          </div>
        )}
        {access === "disabled" && <div className="notice info">AI-копирайтер сейчас выключен администратором.</div>}
      </form>

      <div className={styles.panel} aria-live="polite">
        <div className={styles.panelHead}>
          <span className={styles.status}>
            <span className={`${styles.dot} ${task.isRunning ? styles.dotBusy : ""}`} />
            {isSample ? "пример ответа" : STATUS_LABEL[task.status]}
          </span>
          <span className={styles.muted}>3 варианта</span>
        </div>

        {(task.status === "failed" || task.status === "timeout") && (
          <div className="notice error">{task.error}</div>
        )}

        <div className={styles.tabs} role="tablist" aria-label="Варианты">
          {showing.map((v, i) => (
            <button
              key={`${i}-${v.title}`} type="button" role="tab" aria-selected={i === variant}
              className={i === variant ? styles.tabOn : styles.tab} onClick={() => setVariant(i)}
            >
              Вариант {i + 1}
            </button>
          ))}
        </div>

        <dl className={`${styles.fields} ai-variant`}>
          <dt>Заголовок</dt>
          <dd className={styles.headline}>{current.title}</dd>
          <dt>Текст</dt>
          <dd>{current.text}</dd>
          <dt>Призыв к действию</dt>
          <dd className={styles.cta}>{current.cta}</dd>
        </dl>

        <p className={styles.footnote}>
          {isSample
            ? "Это пример. Ваши варианты появятся здесь — их можно перенести в объявление в кабинете."
            : "Перенесите понравившийся вариант в объявление в кабинете."}
        </p>
      </div>
    </div>
  );
}
