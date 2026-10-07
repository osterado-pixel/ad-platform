import Link from "next/link";

import { DemoWidget } from "@/components/DemoWidget";
import styles from "./page.module.css";

const STEPS = [
  { title: "Зарегистрируйтесь", text: "Аккаунт рекламодателя — за минуту, без договоров и абонентской платы." },
  { title: "Создайте объявление", text: "Заголовок, текст, картинка и ссылка. Текст поможет составить AI-копирайтер." },
  { title: "Пройдите модерацию", text: "Объявление проверяет модератор; ИИ заранее подсвечивает возможные нарушения." },
  { title: "Платите за клики", text: "Баннер показывается на сайтах-партнёрах. Списание — только за переходы на ваш сайт." },
];

const FEATURES = [
  {
    title: "Оплата только за клики",
    text: "Показы бесплатны. Повторные клики с одного адреса и клики ботов не оплачиваются.",
  },
  {
    title: "AI-копирайтер",
    text: "Опишите товар — ИИ предложит три варианта заголовка и текста. Платите по факту — обычно около цента.",
  },
  {
    title: "Честный баланс",
    text: "Каждое списание и пополнение — в истории кошелька. Если генерация не удалась, деньги возвращаются.",
  },
  {
    title: "Статистика по дням",
    text: "Показы, клики, CTR и расход по каждой кампании — в кабинете.",
  },
];

const FAQ = [
  {
    q: "Есть ли абонентская плата?",
    a: "Нет. Вы пополняете баланс и тратите его на клики и AI-генерации. Неиспользованные деньги остаются на балансе.",
  },
  {
    q: "Сколько стоит клик?",
    a: "Цену клика задаёт каждая площадка — её видно при создании кампании до запуска.",
  },
  {
    q: "Что будет, если деньги закончатся?",
    a: "Показы приостановятся сами и возобновятся после пополнения. В минус баланс не уходит.",
  },
  {
    q: "Можно ли разместить рекламу у себя на сайте?",
    a: "Да, подключение площадок — через нас: напишите, и мы выдадим код для вставки баннера.",
  },
];

export default function Home() {
  return (
    <>
      <section className={`section ${styles.hero}`}>
        <div className="container">
          <span className={styles.badge}>Рекламная сеть с AI-копирайтером</span>
          <h1>
            Реклама на сайтах-партнёрах.
            <br />
            Платите только за клики.
          </h1>
          <p className={`muted ${styles.lead}`}>
            Создайте объявление за пару минут — текст поможет составить ИИ. Показы бесплатны,
            списание только за переходы на ваш сайт.
          </p>
          <div className={styles.actions}>
            <a href="/app" className="btn primary">
              Начать бесплатно
            </a>
            <Link href="#demo" className="btn">
              Посмотреть пример
            </Link>
          </div>
        </div>
      </section>

      <section id="how" className="section">
        <div className="container">
          <h2 className="center">Как это работает</h2>
          <ol className={styles.steps}>
            {STEPS.map((s, i) => (
              <li key={s.title} className="card">
                <span className={styles.stepNo}>{i + 1}</span>
                <h3>{s.title}</h3>
                <p className="muted">{s.text}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="section">
        <div className="container grid">
          {FEATURES.map((f) => (
            <div key={f.title} className="card">
              <h3>{f.title}</h3>
              <p className="muted">{f.text}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="demo" className="section">
        <div className="container">
          <h2 className="center">AI-копирайтер в деле</h2>
          <p className="muted center">Выберите пример товара и вариант текста — справа превью баннера.</p>
          <DemoWidget />
        </div>
      </section>

      <section id="pricing" className="section">
        <div className="container">
          <h2 className="center">Цены</h2>
          <div className={`grid ${styles.pricing}`}>
            <div className="card">
              <h3>Клики</h3>
              <p className={styles.price}>по цене площадки</p>
              <p className="muted">Цена известна до запуска кампании. Показы — бесплатно.</p>
            </div>
            <div className="card">
              <h3>AI-копирайтер</h3>
              <p className={styles.price}>по факту</p>
              <p className="muted">
                Оплата по реальному расходу модели — обычно около цента за три варианта. Не получилось —
                деньги возвращаются.
              </p>
            </div>
            <div className="card">
              <h3>Аккаунт</h3>
              <p className={styles.price}>бесплатно</p>
              <p className="muted">Без абонентской платы и минимального бюджета.</p>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2 className="center">Вопросы</h2>
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
          <a href="/app" className="btn primary">
            Создать аккаунт
          </a>
        </div>
      </section>
    </>
  );
}
