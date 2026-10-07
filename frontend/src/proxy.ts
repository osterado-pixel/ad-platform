import { NextResponse, type NextRequest } from "next/server";

import { DEFAULT_LOCALE, isLocale, LOCALE_COOKIE, localeFromAcceptLanguage } from "@/i18n/config";

// Адрес без языка (/, /generate) → /<язык>/…: сначала выбор пользователя (cookie), затем язык браузера,
// иначе — английский. Пути бэкенда (кабинет, API, виджет) сюда не попадают — см. matcher
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const first = pathname.split("/")[1];
  if (isLocale(first)) return NextResponse.next();

  const chosen = request.cookies.get(LOCALE_COOKIE)?.value;
  const locale = isLocale(chosen) ? chosen
    : localeFromAcceptLanguage(request.headers.get("accept-language")) ?? DEFAULT_LOCALE;
  const url = request.nextUrl.clone();
  url.pathname = `/${locale}${pathname === "/" ? "" : pathname}`;
  return NextResponse.redirect(url);
}

export const config = {
  // Не трогаем: файлы Next.js, бэкенд (передаётся rewrites / Caddy) и файлы с расширением
  matcher: ["/((?!_next/|api/|app$|app/|static/|widget\\.js|demo$|docs$|docs/|redoc$|openapi\\.json|favicon\\.ico|.*\\.[a-z0-9]+$).*)"],
};
