import type { Metadata } from "next";
import Link from "next/link";

import { LiveDemo } from "@/components/LiveDemo";
import styles from "./page.module.css";

export const metadata: Metadata = {
  // Полный заголовок, без шаблона «%s — Ad Platform»
  title: { absolute: "Ad Platform — реклама на сайтах-партнёрах и AI-копирайтер" },
  description:
    "Объявление за пару минут: ИИ составит три варианта текста за несколько секунд. Показы бесплатно, " +
    "оплата только за клики, генерация — по факту, при сбое деньги возвращаются.",
};

const STEPS = [
  { title: "Зарегистрируйтесь", text: "Аккаунт рекламодателя — за минуту, без договоров и абонентской платы." },
  { title: "Составьте объявление", text: "Опишите товар — AI-копирайтер предложит три варианта заголовка и текста." },
  { title: "Пройдите модерацию", text: "Объявление проверяет модератор; ИИ заранее подсвечивает возможные нарушения." },
  { title: "Платите за клики", text: "Баннер показывается на сайтах-партнёрах. Списание — только за переходы к вам." },
];

const FEATURES = [
  { title: "Честная оплата", text: "Показы бесплатны. Повторные клики с одного адреса и клики ботов не оплачиваются." },
  { title: "Защита денег", text: "На время генерации сумма замораживается и списывается по факту. Сбой — возвращается вся." },
  { title: "Telegram-бот", text: "Баланс и AI-копирайтер прямо в Telegram — привязка кодом из кабинета, без пароля." },
  { title: "Статистика по дням", text: "Показы, клики, CTR и расход по каждой кампании — в кабинете." },
];

const PLANS = [
  {
    name: "Аккаунт", price: "0", unit: "навсегда",
    note: "Для старта", items: ["Без абонентской платы", "Без минимального бюджета", "Кабинет и статистика"],
    cta: "Создать аккаунт", primary: false,
  },
  {
    name: "AI-копирайтер", price: "≈ 1 цент", unit: "за 3 варианта",
    note: "Оплата по факту", items: ["По реальному расходу модели", "Сбой — деньги возвращаются", "На сайте и в Telegram-боте"],
    cta: "Попробовать", primary: true,
  },
  {
    name: "Реклама", price: "за клик", unit: "по цене площадки",
    note: "Цена видна до запуска", items: ["Показы — бесплатно", "Повторы и боты не оплачиваются", "Закончились деньги — пауза, не долг"],
    cta: "Запустить кампанию", primary: false,
  },
];

const FAQ = [
  { q: "Есть ли абонентская плата?", a: "Нет. Вы пополняете баланс и тратите его на клики и AI-генерации. Остаток остаётся на балансе." },
  { q: "Сколько стоит клик?", a: "Цену клика задаёт каждая площадка — её видно при создании кампании, до запуска." },
  { q: "Что будет, если генерация не удалась?", a: "Замороженная сумма вернётся на баланс целиком — это видно в истории кошелька." },
  { q: "Что будет, если деньги закончатся?", a: "Показы приостановятся сами и возобновятся после пополнения. В минус баланс не уходит." },
  { q: "Можно разместить рекламу у себя на сайте?", a: "Да, площадки подключаем вручную: напишите нам — выдадим код для вставки баннера." },
];

export default function Home() {
  return (
    <>
      {/* 1. Первый экран */}
      <section className={styles.hero}>
        <div className={styles.glow} aria-hidden="true" />
        <div className={`container ${styles.heroInner}`}>
          <span className={styles.badge}>
            <span className={styles.badgeDot} />
            Рекламная сеть с AI-копирайтером
          </span>
          <h1 className={styles.title}>
            Объявление, которое кликают, — за{" "}
            <span className={styles.gradientText}>несколько секунд</span>
          </h1>
          <p className={styles.lead}>
            ИИ составит три варианта заголовка и текста, баннер покажется на сайтах-партнёрах,
            а платите вы только за клики. На время генерации деньги замораживаются и при сбое возвращаются.
          </p>
          <div className={styles.actions}>
            <Link href="#demo" className="btn primary">Попробовать</Link>
            <Link href="#pricing" className="btn">Цены</Link>
          </div>
        </div>
      </section>

      {/* 2. Живое демо */}
      <section id="demo" className="section">
        <div className="container">
          <div className={styles.sectionHead}>
            <h2>AI-копирайтер в деле</h2>
            <p className="muted">Опишите товар — справа появятся варианты. Пока вы не вошли, там пример ответа.</p>
          </div>
          <LiveDemo />
        </div>
      </section>

      {/* 3. Как это работает */}
      <section id="how" className="section">
        <div className="container">
          <div className={styles.sectionHead}><h2>Как это работает</h2></div>
          <ol className={styles.steps}>
            {STEPS.map((s, i) => (
              <li key={s.title} className="card">
                <span className={styles.stepNo}>{i + 1}</span>
                <h3>{s.title}</h3>
                <p className="muted">{s.text}</p>
              </li>
            ))}
          </ol>
          <div className={`grid ${styles.features}`}>
            {FEATURES.map((f) => (
              <div key={f.title} className="card">
                <h3>{f.title}</h3>
                <p className="muted">{f.text}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* 4. Цены */}
      <section id="pricing" className="section">
        <div className="container">
          <div className={styles.sectionHead}>
            <h2>Прозрачные цены</h2>
            <p className="muted">Без подписок и тарифных ловушек: платите только за то, что использовали.</p>
          </div>
          <div className={styles.plans}>
            {PLANS.map((p) => (
              <div key={p.name} className={`${styles.plan} ${p.primary ? styles.planPrimary : ""}`}>
                {p.primary && <span className={styles.popular}>Популярное</span>}
                <h3>{p.name}</h3>
                <p className="muted">{p.note}</p>
                <div className={styles.price}>
                  {p.price} <span>{p.unit}</span>
                </div>
                <ul className={styles.planItems}>
                  {p.items.map((item) => <li key={item}>{item}</li>)}
                </ul>
                <a href={p.primary ? "#demo" : "/app"} className={`btn ${p.primary ? "primary" : ""} ${styles.planCta}`}>
                  {p.cta}
                </a>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* 5. Вопросы */}
      <section className="section">
        <div className="container">
          <div className={styles.sectionHead}><h2>Вопросы</h2></div>
          <div className={styles.faq}>
            {FAQ.map((item) => (
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
          <h2>Запустите первую кампанию сегодня</h2>
          <a href="/app" className="btn primary">Создать аккаунт</a>
        </div>
      </section>
    </>
  );
}
