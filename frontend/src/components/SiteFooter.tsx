import Link from "next/link";

import type { Locale } from "@/i18n/config";
import type { Dictionary } from "@/i18n/dictionaries";

export function SiteFooter({ lang, t }: { lang: Locale; t: Dictionary["footer"] }) {
  return (
    <footer style={{ borderTop: "1px solid var(--border)", padding: "32px 0", marginTop: 32 }}>
      <div className="container muted" style={{ display: "flex", gap: 16, flexWrap: "wrap", justifyContent: "space-between" }}>
        <span>© Ad Platform</span>
        <span style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <a href="/app">{t.cabinet}</a>
          <Link href={`/${lang}/partners`}>{t.partners}</Link>
          <a href="/demo?placement=habr_main_banner">{t.bannerExample}</a>
          <a href="/docs">{t.api}</a>
        </span>
      </div>
    </footer>
  );
}
