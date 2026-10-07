"use client";

import { useState } from "react";

import styles from "./DemoWidget.module.css";

// Пример работы AI-копирайтера — заранее подготовленные ответы, без запросов к API:
// посетитель сайта не тратит ничьи деньги, а настоящая генерация — после входа (/generate)
const EXAMPLES = [
  {
    product: "Онлайн-курс Python с нуля: 40 уроков, практика, сертификат",
    variants: [
      { title: "Python за 3 месяца", text: "40 уроков с практикой и наставником. Сертификат в конце", cta: "Начать учиться" },
      { title: "Первая программа — сегодня", text: "Курс для новичков: от основ до своего проекта", cta: "Записаться" },
      { title: "Профессия с нуля", text: "Практика с первого урока и разбор ваших задач", cta: "Попробовать бесплатно" },
    ],
  },
  {
    product: "Кофейня у метро: зерно свежей обжарки, завтраки до 12:00",
    variants: [
      { title: "Кофе свежей обжарки", text: "В двух минутах от метро. Завтраки до полудня", cta: "Построить маршрут" },
      { title: "Утро начинается здесь", text: "Свежая обжарка каждую неделю и тёплые завтраки", cta: "Посмотреть меню" },
      { title: "Ваш кофе по пути", text: "Возьмите с собой — приготовим, пока идёте", cta: "Заказать заранее" },
    ],
  },
  {
    product: "Ремонт ноутбуков: диагностика бесплатно, гарантия 6 месяцев",
    variants: [
      { title: "Ноутбук не включается?", text: "Бесплатная диагностика и гарантия полгода", cta: "Записаться на ремонт" },
      { title: "Ремонт за 1 день", text: "Чистка, замена экрана и клавиатуры с гарантией", cta: "Узнать цену" },
      { title: "Вернём ноутбук к жизни", text: "Честная диагностика — платите только за ремонт", cta: "Оставить заявку" },
    ],
  },
];

export function DemoWidget() {
  const [exampleIndex, setExampleIndex] = useState(0);
  const [variantIndex, setVariantIndex] = useState(0);
  const example = EXAMPLES[exampleIndex];
  const variant = example.variants[variantIndex];

  return (
    <div className={`card ${styles.demo}`}>
      <div>
        <label htmlFor="demo-product">Что рекламируете</label>
        <div className={styles.tabs} role="tablist" id="demo-product">
          {EXAMPLES.map((e, i) => (
            <button
              key={e.product}
              type="button"
              role="tab"
              aria-selected={i === exampleIndex}
              className={i === exampleIndex ? styles.tabOn : styles.tab}
              onClick={() => { setExampleIndex(i); setVariantIndex(0); }}
            >
              {e.product.split(":")[0]}
            </button>
          ))}
        </div>
        <p className={`muted ${styles.product}`}>«{example.product}»</p>
        <div className={styles.variants}>
          {example.variants.map((v, i) => (
            <button
              key={v.title}
              type="button"
              className={i === variantIndex ? styles.variantOn : styles.variant}
              onClick={() => setVariantIndex(i)}
            >
              <b>{v.title}</b>
              <span>{v.text}</span>
            </button>
          ))}
        </div>
      </div>
      <div>
        <div className={`muted ${styles.caption}`}>Так баннер увидят посетители сайта-партнёра</div>
        <div className={styles.banner}>
          <div className={styles.bannerTitle}>{variant.title}</div>
          <div>{variant.text}</div>
          <span className={styles.bannerCta}>{variant.cta} →</span>
          <span className={styles.adLabel}>Реклама</span>
        </div>
        <p className={`muted ${styles.note}`}>
          Пример заранее подготовлен. В кабинете ИИ составит варианты для вашего товара за несколько секунд.
        </p>
      </div>
    </div>
  );
}
