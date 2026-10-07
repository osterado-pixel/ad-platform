// Языки сайта. Новый язык — код здесь и словарь src/i18n/dictionaries/<код>.json (проверка — npm run check:i18n)
export const LOCALES = ["en", "ru", "de"] as const;
export type Locale = (typeof LOCALES)[number];

// Широкий рынок: без подсказок браузера — английский
export const DEFAULT_LOCALE: Locale = "en";

// Выбор пользователя в переключателе (функциональная настройка — без согласия на cookie)
export const LOCALE_COOKIE = "lang";

export const LOCALE_NAMES: Record<Locale, string> = { en: "English", ru: "Русский", de: "Deutsch" };

// Формат чисел и денег
export const NUMBER_LOCALE: Record<Locale, string> = { en: "en-US", ru: "ru-RU", de: "de-DE" };

export function isLocale(value: string | undefined | null): value is Locale {
  return !!value && (LOCALES as readonly string[]).includes(value);
}

/** Язык из заголовка Accept-Language («de-DE,de;q=0.9,en;q=0.8») — первый поддерживаемый по весу. */
export function localeFromAcceptLanguage(header: string | null): Locale | null {
  if (!header) return null;
  const ranked = header
    .split(",")
    .map((part) => {
      const [tag, ...params] = part.trim().split(";");
      const q = params.map((p) => p.trim()).find((p) => p.startsWith("q="));
      return { lang: tag.toLowerCase().split("-")[0], q: q ? Number(q.slice(2)) || 0 : 1 };
    })
    .filter((x) => x.lang && x.q > 0)
    .sort((a, b) => b.q - a.q);
  return ranked.map((x) => x.lang).find(isLocale) ?? null;
}
