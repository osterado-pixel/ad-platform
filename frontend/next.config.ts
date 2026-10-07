import type { NextConfig } from "next";

// Адрес бэкенда (FastAPI). Конфиг читается при сборке (`next build`) и вшивается в сервер,
// поэтому в Docker адрес задаётся аргументом сборки BACKEND_URL (см. Dockerfile)
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

// Всё, что не страницы фронтенда, — бэкенд: API, кабинет /app (у него свой интерфейс), виджет,
// документация API. Фронтенд и кабинет — на одном домене: вход общий (токен в localStorage),
// CORS не нужен. На боевом сервере эти пути до фронтенда не доходят — их сразу отдаёт Caddy
const BACKEND_PATHS = [
  "/api/:path*",
  "/app",
  "/static/:path*",
  "/widget.js",
  "/demo",
  "/docs",
  "/openapi.json",
];

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  // Docker: в образ попадает только нужное для работы (.next/standalone), без всего node_modules
  output: "standalone",
  // Не сообщаем посетителям, на чём сделан сайт
  poweredByHeader: false,
  async rewrites() {
    return BACKEND_PATHS.map((source) => ({ source, destination: `${BACKEND_URL}${source}` }));
  },
};

export default nextConfig;
