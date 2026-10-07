import "server-only";

import type { Locale } from "./config";
import type ru from "./dictionaries/ru.json";

// Русский словарь задаёт структуру; полноту остальных проверяет npm run check:i18n (и CI)
export type Dictionary = typeof ru;

const dictionaries: Record<Locale, () => Promise<Dictionary>> = {
  en: () => import("./dictionaries/en.json").then((m) => m.default),
  ru: () => import("./dictionaries/ru.json").then((m) => m.default),
  de: () => import("./dictionaries/de.json").then((m) => m.default),
};

export const getDictionary = (locale: Locale): Promise<Dictionary> => dictionaries[locale]();
