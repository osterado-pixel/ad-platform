export function SiteFooter() {
  return (
    <footer style={{ borderTop: "1px solid var(--border)", padding: "32px 0", marginTop: 32 }}>
      <div className="container muted" style={{ display: "flex", gap: 16, flexWrap: "wrap", justifyContent: "space-between" }}>
        <span>© Ad Platform</span>
        <span style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <a href="/app">Кабинет</a>
          <a href="/demo?placement=habr_main_banner">Пример баннера на сайте</a>
          <a href="/docs">API для разработчиков</a>
        </span>
      </div>
    </footer>
  );
}
