import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // /app, /docs, /demo, /api отдаёт бэкенд (rewrites / Caddy), а не страницы сайта: для них нужна
      // обычная ссылка <a> — <Link> попытался бы открыть их как страницу Next.js. Правило ошибочно
      // считает их страницами из-за маршрута app/[lang]/[...rest]
      "@next/next/no-html-link-for-pages": "off",
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
