import { NUMBER_LOCALE, type Locale } from "./config";

/** Подстановка в шаблон перевода: fmt("Вариант {n}", { n: 2 }) → "Вариант 2". */
export function fmt(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) => (key in vars ? String(vars[key]) : match));
}

/** Деньги в формате языка: 1234.5 → «1 234,50» (ru), «1,234.50» (en), «1.234,50» (de). */
export function money(value: number, locale: Locale): string {
  return value.toLocaleString(NUMBER_LOCALE[locale], { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
