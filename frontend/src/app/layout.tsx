import type { Metadata } from "next";

import { SiteFooter } from "@/components/SiteFooter";
import { SiteHeader } from "@/components/SiteHeader";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Ad Platform — реклама на сайтах-партнёрах с оплатой за клик",
    template: "%s — Ad Platform",
  },
  description:
    "Рекламная сеть: размещайте баннеры на сайтах-партнёрах и платите только за клики. " +
    "AI-копирайтер составит текст объявления, модерация — за минуты.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ru">
      <body>
        <SiteHeader />
        <main>{children}</main>
        <SiteFooter />
      </body>
    </html>
  );
}
