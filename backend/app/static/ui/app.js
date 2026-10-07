"use strict";
/*
 * Ad Platform — веб-интерфейс (без сборки, без зависимостей).
 * Все данные выводятся через textContent (функция h), innerHTML не используется:
 * текст рекламодателей и пользователей не может выполниться как код.
 * Строки интерфейса — через t() / tx() из i18n.js (словари EN/RU/DE, загружается раньше).
 */

const API = "/api/v1";
const TOKEN_KEY = "adp_token";

const state = { token: readToken(), me: null, placements: null };

// ---------- Хранилище токена ----------
function readToken() {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}
function writeToken(token) {
  state.token = token;
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token); else localStorage.removeItem(TOKEN_KEY);
  } catch { /* приватный режим: токен живёт до закрытия вкладки */ }
}

// ---------- DOM ----------
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key in el && typeof value !== "string") el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

// parent.append() превращает null в текст "null" — добавляем только реальные узлы
function add(parent, ...children) {
  parent.append(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
  return parent;
}

function toast(message, kind) {
  const box = document.getElementById("toasts");
  const el = h("div", { class: "toast" + (kind === "error" ? " error" : ""), role: "status" }, message);
  box.append(el);
  setTimeout(() => el.remove(), kind === "error" ? 6000 : 3000);
}

// ---------- API ----------
class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function errorText(data) {
  const d = data && data.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d.map((e) => {
      const field = (e.loc || []).filter((x) => x !== "body" && x !== "query").join(".");
      const msg = String(e.msg || "").replace(/^Value error, /, "");
      return field ? `${field}: ${msg}` : msg;
    }).join("; ");
  }
  return "";
}

async function api(method, path, body, opts = {}) {
  const headers = { "Accept-Language": locale };
  if (state.token) headers.Authorization = "Bearer " + state.token;
  let payload;
  if (opts.form) {
    payload = new URLSearchParams(body);
  } else if (body !== undefined) {
    payload = JSON.stringify(body);
    headers["Content-Type"] = "application/json";
  }
  let response;
  try {
    response = await fetch(API + path, { method, headers, body: payload });
  } catch {
    throw new ApiError(0, t("api.serverDown"));
  }
  if (response.status === 204) return opts.page ? { items: [], hasMore: false, next: null } : null;
  let data = null;
  try { data = await response.json(); } catch { /* пустой ответ */ }
  if (!response.ok) {
    if (response.status === 401 && state.token && !opts.keepSession) {
      logout(t("api.sessionExpired"));
    }
    throw new ApiError(response.status, errorText(data) || t("api.error", { status: response.status }));
  }
  if (opts.page) {
    // Список постранично: X-Has-More — есть ли ещё, X-Next-Before-Id — курсор следующей страницы
    return {
      items: Array.isArray(data) ? data : data.items,
      hasMore: response.headers.get("X-Has-More") === "true",
      next: response.headers.get("X-Next-Before-Id"),
    };
  }
  return data;
}

// Список PaginatedResponse с подгрузкой по offset: кнопка «Показать ещё» и первая страница
async function offsetFeed(path, limit, renderRow, tbody) {
  let offset = 0;
  let total = 0;
  const more = h("button", { class: "small" }, t("common.showMore"));
  const load = async () => {
    const sep = path.includes("?") ? "&" : "?";
    const page = await api("GET", `${path}${sep}limit=${limit}&offset=${offset}`);
    page.items.forEach((item) => tbody.append(renderRow(item)));
    offset += page.items.length;
    total = page.total;
    more.hidden = offset >= total || !page.items.length;
    return page.items.length;
  };
  more.onclick = (e) => run(e.currentTarget, load);
  const first = await load();
  return { more, first, total: () => total };
}

// Лента с подгрузкой по курсору: возвращает кнопку «Показать ещё» и первую страницу
async function cursorFeed(path, limit, renderRow, tbody) {
  let cursor = null;
  const more = h("button", { class: "small" }, t("common.showMore"));
  const load = async () => {
    const sep = path.includes("?") ? "&" : "?";
    const page = await api("GET", `${path}${sep}limit=${limit}${cursor ? "&before_id=" + cursor : ""}`,
      undefined, { page: true });
    page.items.forEach((item) => tbody.append(renderRow(item)));
    cursor = page.next;
    more.hidden = !page.hasMore;
    return page.items.length;
  };
  more.onclick = (e) => run(e.currentTarget, load);
  const first = await load();
  return { more, first };
}

// Выполняет действие с блокировкой кнопки и сообщением об ошибке
async function run(button, action, successMessage) {
  if (button) button.disabled = true;
  try {
    const result = await action();
    if (successMessage) toast(successMessage);
    return result;
  } catch (err) {
    toast(err.message, "error");
    return undefined;
  } finally {
    if (button && button.isConnected) button.disabled = false;
  }
}

// ---------- Форматирование ----------
// Статус кампании и тип операции: известный код — перевод, новый (сервер новее кабинета) — как есть
const known = (prefix, code) => (MESSAGES[DEFAULT_LOCALE][`${prefix}.${code}`] !== undefined ? t(`${prefix}.${code}`) : code);
const TX_SPEND = new Set(["click_spend", "ai_spend"]);  // списания — со знаком «−»

const money = (v) => Number(v || 0).toLocaleString(numberLocale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const int = (v) => Number(v || 0).toLocaleString(numberLocale());
// CTR по объекту статистики: без показов — прочерк (API в этом случае отдаёт 0)
const ctr = (s) => (s && s.impressions ? `${s.ctr.toLocaleString(numberLocale())}%` : "—");
const statusBadge = (s) => h("span", { class: `badge ${s}` }, known("status", s));

function dateTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString(numberLocale(), { dateStyle: "short", timeStyle: "short" });
}
// День статистики «2026-10-07» → «07.10» / «10/07»: дата без времени, часовой пояс не сдвигает её
function shortDay(isoDay) {
  return new Date(`${isoDay}T00:00:00Z`).toLocaleDateString(numberLocale(),
    { day: "2-digit", month: "2-digit", timeZone: "UTC" });
}
// datetime-local <-> ISO (UTC). Сервер хранит UTC, показываем в местном времени
function toLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function fromLocalInput(value) {
  return value ? new Date(value).toISOString() : null;
}

function statCard(label, value) {
  return h("div", { class: "stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value));
}

function table(headers, rows, emptyText) {
  if (!rows.length) return h("div", { class: "empty" }, emptyText || t("common.empty"));
  return h("div", { class: "table-wrap" },
    h("table", {},
      h("thead", {}, h("tr", {}, headers.map((x) => h("th", { class: x.num ? "num" : "" }, x.label || x)))),
      h("tbody", {}, rows)));
}

// Столбчатый график: показы (светлые) и клики (яркие) по дням
function chart(days) {
  if (!days.length) return h("div", { class: "empty" }, t("common.noData"));
  const W = 720, H = 200, top = 10, bottom = 22, left = 34;
  // Целые деления шкалы: при 3 показах — 0,1,2,3,4, а не 0,1,2,2,3 после округления
  const maxImp = Math.ceil(Math.max(1, ...days.map((d) => d.impressions)) / 4) * 4;
  const maxClk = Math.max(1, ...days.map((d) => d.clicks));
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "chart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", t("chart.aria"));
  const s = (tag, attrs, text) => {
    const el = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    if (text !== undefined) el.textContent = text;
    svg.append(el);
    return el;
  };
  const plotH = H - top - bottom;
  const step = (W - left) / days.length;
  const bar = Math.max(2, Math.min(18, step * 0.35));
  for (let i = 0; i <= 4; i++) {
    const y = top + (plotH * i) / 4;
    s("line", { x1: left, x2: W, y1: y, y2: y, class: "grid-line" });
    s("text", { x: left - 4, y: y + 3, "text-anchor": "end", class: "axis" }, int(maxImp * (1 - i / 4)));
  }
  const labelEvery = Math.ceil(days.length / 12);
  days.forEach((d, i) => {
    const x = left + i * step + step / 2;
    const hi = (d.impressions / maxImp) * plotH;
    const hc = (d.clicks / maxClk) * plotH * 0.9;
    const tip = t("chart.tip", { day: shortDay(d.day), impressions: int(d.impressions), clicks: int(d.clicks), spend: money(d.spend) });
    s("rect", { x: x - bar, y: top + plotH - hi, width: bar, height: hi, class: "a" }).append(
      Object.assign(document.createElementNS(ns, "title"), { textContent: tip }));
    s("rect", { x: x, y: top + plotH - hc, width: bar, height: hc, class: "b" }).append(
      Object.assign(document.createElementNS(ns, "title"), { textContent: tip }));
    if (i % labelEvery === 0) s("text", { x, y: H - 6, "text-anchor": "middle", class: "axis" }, shortDay(d.day));
  });
  return h("div", {},
    h("div", { class: "legend" },
      h("span", {}, h("i", { style: "background:var(--chart-a)" }), t("chart.impressions")),
      h("span", {}, h("i", { style: "background:var(--chart-b)" }), t("chart.clicks"))),
    svg);
}

function daysSelect(current, onChange) {
  return h("select", { style: "width:auto", onchange: (e) => onChange(Number(e.target.value)) },
    [7, 30, 90].map((n) => h("option", { value: n, selected: n === current }, t("common.days", { n }))));
}

function adPreview(c) {
  return h("div", { class: "ad-preview" },
    c.image_url ? h("img", { src: c.image_url, alt: "", loading: "lazy" }) : null,
    h("div", {}, h("div", { class: "t" }, c.title), c.description ? h("div", { class: "d" }, c.description) : null));
}

async function loadPlacements(force) {
  if (!state.placements || force) state.placements = (await api("GET", "/placements?limit=500")).items;
  return state.placements;
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast(t("common.copied")); }
  catch { toast(t("common.copyFailed"), "error"); }
}

// ---------- Вход и регистрация ----------
// Реферальный код из ссылки приглашения (/app?ref=КОД). Запоминаем в браузере: человек может
// зарегистрироваться не сразу. Хранилище может быть недоступно (приватный режим) — тогда только из адреса
const REF_KEY = "adp_ref";
const urlRef = new URLSearchParams(location.search).get("ref");
function referralCode() {
  try {
    if (urlRef) localStorage.setItem(REF_KEY, urlRef);
    return urlRef || localStorage.getItem(REF_KEY);
  } catch { return urlRef; }
}

function authView() {
  let mode = "login";
  const ref = referralCode();
  const error = h("div");
  const email = h("input", { type: "email", required: true, autocomplete: "email", id: "auth-email" });
  const password = h("input", { type: "password", required: true, minLength: 8, id: "auth-password",
    autocomplete: "current-password" });
  const submit = h("button", { class: "primary", type: "submit", style: "width:100%;justify-content:center" },
    t("auth.submitLogin"));
  const tabs = h("div", { class: "tabs" });
  const setMode = (m) => {
    mode = m;
    submit.textContent = m === "login" ? t("auth.submitLogin") : t("auth.submitRegister");
    password.autocomplete = m === "login" ? "current-password" : "new-password";
    tabs.replaceChildren(
      h("button", { type: "button", class: m === "login" ? "on" : "", onclick: () => setMode("login") }, t("auth.login")),
      h("button", { type: "button", class: m === "register" ? "on" : "", onclick: () => setMode("register") }, t("auth.register")));
    error.replaceChildren();
  };
  setMode(urlRef ? "register" : "login"); // пришли по приглашению — сразу регистрация

  const form = h("form", {
    class: "card",
    onsubmit: async (e) => {
      e.preventDefault();
      error.replaceChildren();
      submit.disabled = true;
      try {
        if (mode === "register") {
          await api("POST", "/auth/register", { email: email.value, password: password.value, ...(ref ? { ref } : {}) });
          try { localStorage.removeItem(REF_KEY); } catch { /* хранилище недоступно */ }
        }
        const token = await api("POST", "/auth/login",
          { username: email.value, password: password.value }, { form: true, keepSession: true });
        writeToken(token.access_token);
        state.me = null;
        go("#/overview");
      } catch (err) {
        error.replaceChildren(h("div", { class: "notice error" }, err.message));
      } finally {
        submit.disabled = false;
      }
    },
  },
  tabs, error,
  h("div", { class: "field" }, h("label", { for: "auth-email" }, t("auth.email")), email),
  h("div", { class: "field" }, h("label", { for: "auth-password" }, t("auth.password")), password,
    h("div", { class: "hint" }, t("auth.passwordHint"))),
  submit);

  return h("div", { class: "auth" }, h("span", { class: "brand" }, "Ad", h("span", {}, "Platform")), form,
    h("div", { class: "auth-langs" }, langSwitcher()));
}

// Переключатель языка: тот же выбор (cookie lang), что на сайте. Страница перерисовывается на новом языке
function langSwitcher() {
  return h("div", { class: "langs", role: "group", "aria-label": t("common.language") },
    LOCALES.map((code) => h("button", {
      type: "button", class: code === locale ? "on" : "", lang: code, title: LOCALE_NAMES[code],
      "aria-pressed": String(code === locale),
      onclick: () => { if (code !== locale) { setLocale(code); render(); } },
    }, code.toUpperCase())));
}

function logout(message) {
  writeToken(null);
  state.me = null;
  state.placements = null;
  if (message) toast(message, "error");
  go("");
}

// Переход: смена адреса сама вызовет render() (hashchange); если адрес тот же — рисуем явно
function go(hash) {
  if (location.hash === hash || (!hash && !location.hash)) render();
  else location.hash = hash;
}

// ---------- Каркас ----------
const ROUTES = [
  { path: "#/overview", title: "nav.overview", view: overviewView },
  { path: "#/campaigns", title: "nav.campaigns", view: campaignsView },
  { path: "#/wallet", title: "nav.wallet", view: walletView },
  { path: "#/admin/moderation", title: "nav.moderation", view: moderationView, admin: true },
  { path: "#/admin/placements", title: "nav.placements", view: placementsView, admin: true },
  { path: "#/admin/users", title: "nav.users", view: usersView, admin: true },
  { path: "#/admin/platform", title: "nav.platform", view: platformView, admin: true },
];

function matchRoute(hash) {
  let m;
  if (hash === "#/profile") return { view: profileView, nav: null };
  if ((m = hash.match(/^#\/campaigns\/new$/))) return { view: () => campaignFormView(null), nav: "#/campaigns" };
  if ((m = hash.match(/^#\/campaigns\/(\d+)\/edit$/))) return { view: () => campaignFormView(Number(m[1])), nav: "#/campaigns" };
  if ((m = hash.match(/^#\/campaigns\/(\d+)$/))) return { view: () => campaignDetailView(Number(m[1])), nav: "#/campaigns" };
  const route = ROUTES.find((r) => r.path === hash);
  if (route && (!route.admin || state.me.role === "admin")) return { view: route.view, nav: route.path };
  return null;
}

let renderSeq = 0;
async function render() {
  const seq = ++renderSeq;
  const root = document.getElementById("app");
  if (!state.token) {
    root.replaceChildren(authView());
    return;
  }
  try {
    // Профиль — при каждом переходе: баланс в шапке и роль всегда актуальны
    state.me = await api("GET", "/auth/me");
  } catch {
    if (!state.token) return; // токен недействителен: logout() уже показал форму входа
    if (seq !== renderSeq) return;
    root.replaceChildren(h("div", { class: "auth" }, h("div", { class: "notice error" },
      t("app.profileError"))));
    return;
  }
  // Пока ждали профиль, мог начаться более новый переход: устаревшая отрисовка ничего не трогает,
  // иначе она затрёт страницу поверх новой (ответы сервера приходят в любом порядке)
  if (seq !== renderSeq) return;
  const route = matchRoute(location.hash);
  if (!route) {
    location.replace("#/overview");
    return;
  }
  const main = h("main", {}, h("p", { class: "muted" }, t("common.loading")));
  root.replaceChildren(topbar(), navbar(route.nav), main);
  try {
    const content = await route.view();
    if (seq === renderSeq) main.replaceChildren(content);
  } catch (err) {
    if (seq === renderSeq) main.replaceChildren(h("div", { class: "notice error" }, err.message));
  }
}

function topbar() {
  const me = state.me;
  return h("header", { class: "topbar" },
    h("a", { class: "brand", href: "#/overview" }, "Ad", h("span", {}, "Platform")),
    h("div", { class: "spacer" }),
    h("div", { class: "who" },
      h("div", {}, h("a", { href: "#/profile", title: t("topbar.profile") }, me.email),
        me.role === "admin" ? ` · ${t("topbar.admin")}` : ""),
      h("div", {}, tx("topbar.balance", { amount: h("b", { id: "balance" }, money(me.balance)) }))),
    langSwitcher(),
    h("button", { class: "small", onclick: () => logout() }, t("topbar.logout")));
}

function navbar(active) {
  const nav = h("nav", { class: "nav" });
  ROUTES.forEach((r, i) => {
    if (r.admin && state.me.role !== "admin") return;
    if (r.admin && !ROUTES[i - 1].admin) nav.append(h("span", { class: "sep" }));
    nav.append(h("a", { href: r.path, class: r.path === active ? "active" : "" }, t(r.title)));
  });
  return nav;
}

async function refreshMe() {
  state.me = await api("GET", "/auth/me");
  const el = document.getElementById("balance");
  if (el) el.textContent = money(state.me.balance);
}

// ---------- Обзор ----------
async function overviewView(days = 30) {
  const s = await api("GET", `/stats/me?days=${days}`);
  const wrap = h("div");
  const reload = async (n) => wrap.replaceWith(await overviewView(n));
  const active = s.campaigns_by_status.active || 0;
  const lowBalance = s.campaigns.some((c) => c.status === "active") && Number(s.balance) <= 0;

  add(wrap,
    h("div", { class: "row between" }, h("h1", {}, t("nav.overview")), daysSelect(days, reload)),
    lowBalance ? h("div", { class: "notice warn" }, t("overview.lowBalance")) : null,
    h("div", { class: "grid" },
      statCard(t("stat.balance"), money(s.balance)),
      statCard(t("stat.activeCampaigns"), int(active)),
      statCard(t("stat.impressions"), int(s.totals.impressions)),
      statCard(t("stat.clicks"), int(s.totals.clicks)),
      statCard(t("stat.ctr"), ctr(s.totals)),
      statCard(t("stat.spend"), money(s.totals.spend))),
    h("div", { class: "card" }, h("h2", {}, t("common.byDays")), chart(s.days)),
    h("div", { class: "card" },
      h("div", { class: "row between" }, h("h2", {}, t("overview.campaigns")),
        h("a", { class: "btn small primary", href: "#/campaigns/new" }, t("campaign.new"))),
      table([t("col.campaign"), t("col.status"), { label: t("col.impressions"), num: 1 }, { label: t("col.clicks"), num: 1 },
        { label: t("col.ctr"), num: 1 }, { label: t("col.spend"), num: 1 }],
      s.campaigns.map((c) => h("tr", {},
        h("td", {}, h("a", { href: `#/campaigns/${c.campaign_id}` }, c.title)),
        h("td", {}, statusBadge(c.status)),
        h("td", { class: "num" }, int(c.impressions)), h("td", { class: "num" }, int(c.clicks)),
        h("td", { class: "num" }, ctr(c)), h("td", { class: "num" }, money(c.spend)))),
      t("overview.noCampaigns")),
      s.campaigns_has_more ? h("p", { class: "small muted" },
        tx("overview.hasMore", { link: h("a", { href: "#/campaigns" }, t("overview.hasMoreLink")) })) : null));
  return wrap;
}

// ---------- Кампании ----------
function campaignActions(c, onChange) {
  const act = (label, method, path, opts = {}) => h("button", {
    class: "small" + (opts.danger ? " danger" : "") + (opts.primary ? " primary" : ""),
    onclick: async (e) => {
      if (opts.confirm && !confirm(opts.confirm)) return;
      const result = await run(e.currentTarget, () => api(method, path), opts.done);
      if (result !== undefined) onChange();
    },
  }, label);
  const base = `/campaigns/${c.id}`;
  const edit = h("a", { class: "btn small", href: `#/campaigns/${c.id}/edit` }, t("campaign.edit"));
  const buttons = [];
  if (c.status === "draft" || c.status === "rejected") {
    buttons.push(act(t("campaign.submit"), "POST", `${base}/submit`, { primary: true, done: t("campaign.submitted") }), edit);
    if (!c.impressions_count) {
      buttons.push(act(t("campaign.delete"), "DELETE", base,
        { danger: true, confirm: t("campaign.deleteConfirm", { title: c.title }), done: t("campaign.deleted") }));
    }
  }
  if (c.status === "active") {
    buttons.push(act(t("campaign.pause"), "POST", `${base}/pause`, { done: t("campaign.paused") }), edit);
  }
  if (c.status === "paused") {
    buttons.push(act(t("campaign.resume"), "POST", `${base}/resume`, { primary: true, done: t("campaign.resumed") }), edit);
  }
  if (!["completed", "draft", "rejected"].includes(c.status)) {
    buttons.push(act(t("campaign.complete"), "POST", `${base}/complete`, {
      danger: true, done: t("campaign.completed"),
      confirm: t("campaign.completeConfirm", { title: c.title }),
    }));
  }
  return h("div", { class: "actions" }, buttons);
}

async function campaignsView() {
  const placements = await loadPlacements();
  const placeName = (id) => id == null ? t("campaign.network")
    : (placements.find((p) => p.id === id) || {}).name || t("common.disabledRef", { id });
  const wrap = h("div");
  const reload = async () => wrap.replaceWith(await campaignsView());
  const rows = h("tbody");
  const row = (c) => h("tr", {},
        h("td", {}, h("a", { href: `#/campaigns/${c.id}` }, c.title),
          c.status === "rejected" && c.rejection_reason
            ? h("div", { class: "small", style: "color:var(--danger)" }, t("campaigns.reason", { reason: c.rejection_reason }))
            : null),
        h("td", {}, placeName(c.placement_id)),
        h("td", {}, statusBadge(c.status)),
        h("td", { class: "num" }, int(c.impressions_count)),
        h("td", { class: "num" }, int(c.clicks_count)),
        h("td", {}, campaignActions(c, reload)));
  const feed = await offsetFeed("/campaigns/my", 50, row, rows);
  const headers = [t("col.campaign"), t("col.placement"), t("col.status"), { label: t("col.impressions"), num: 1 },
    { label: t("col.clicks"), num: 1 }, t("col.actions")];
  add(wrap,
    h("div", { class: "row between" }, h("h1", {}, t("nav.campaigns")),
      h("a", { class: "btn primary", href: "#/campaigns/new" }, t("campaign.new"))),
    h("div", { class: "card" },
      feed.first ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, headers.map((x) => h("th", { class: x.num ? "num" : "" }, x.label || x)))), rows))
        : h("div", { class: "empty" }, t("campaigns.empty")),
      h("div", { style: "margin-top:10px" }, feed.more)));
  return wrap;
}

async function campaignDetailView(id, days = 30) {
  const [c, s] = await Promise.all([api("GET", `/campaigns/${id}`), api("GET", `/stats/campaigns/${id}?days=${days}`)]);
  const placements = await loadPlacements();
  const placement = placements.find((p) => p.id === c.placement_id);
  const wrap = h("div");
  const reload = async (n = days) => wrap.replaceWith(await campaignDetailView(id, n));
  add(wrap,
    h("div", { class: "row between" },
      h("h1", {}, c.title, " ", statusBadge(c.status)), daysSelect(days, reload)),
    c.status === "rejected" && c.rejection_reason
      ? h("div", { class: "notice error" }, t("detail.rejected", { reason: c.rejection_reason })) : null,
    c.status === "moderation" ? h("div", { class: "notice warn" }, t("detail.moderation")) : null,
    h("div", { class: "card stack" },
      adPreview(c),
      h("div", { class: "small muted" }, tx("detail.target", {
        url: h("a", { href: c.target_url, target: "_blank", rel: "noopener noreferrer", class: "break" }, c.target_url) })),
      h("div", { class: "small muted" },
        t("detail.placement", { placement: c.placement_id == null ? t("campaign.network") : placement
          ? t("detail.placementPrice", { name: placement.name, price: money(placement.price_per_click) })
          : `#${c.placement_id}` }),
        " · ",
        c.cpc_bid != null ? t("detail.bid", { bid: money(c.cpc_bid) }) : t("detail.bidAuto"),
        " · ",
        t("detail.schedule", {
          start: c.start_date ? t("detail.startFrom", { date: dateTime(c.start_date) }) : t("detail.startNow"),
          end: c.end_date ? t("detail.endAt", { date: dateTime(c.end_date) }) : t("detail.endNever") })),
      campaignActions(c, () => reload())),
    h("div", { class: "grid" },
      statCard(t("stat.impressions"), int(s.totals.impressions)),
      statCard(t("stat.clicks"), int(s.totals.clicks)),
      statCard(t("stat.ctr"), ctr(s.totals)),
      statCard(t("stat.spend"), money(s.totals.spend)),
      statCard(t("stat.totalImpressions"), int(c.impressions_count)),
      statCard(t("stat.totalClicks"), int(c.clicks_count))),
    h("div", { class: "card" }, h("h2", {}, t("common.byDays")), chart(s.days)),
    h("div", { class: "card" }, table([t("col.day"), { label: t("col.impressions"), num: 1 }, { label: t("col.clicks"), num: 1 },
      { label: t("col.ctr"), num: 1 }, { label: t("col.spend"), num: 1 }],
    s.days.slice().reverse().filter((d) => d.impressions || d.clicks).map((d) => h("tr", {},
      h("td", {}, shortDay(d.day)), h("td", { class: "num" }, int(d.impressions)),
      h("td", { class: "num" }, int(d.clicks)), h("td", { class: "num" }, ctr(d)),
      h("td", { class: "num" }, money(d.spend)))),
    t("detail.noEvents"))));
  return wrap;
}

// ---------- AI-копирайтер: фоновая задача + опрос статуса ----------
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const AI_POLL_LIMIT_MS = 150000;  // дольше генерация не идёт: дальше задачу закроет очистка зависших

// Блок над формой кампании. onUse(variant) — подставить выбранный вариант в поля формы
async function aiCopywriterPanel(onUse) {
  const status = await api("GET", "/ai/status").catch(() => ({ enabled: false }));
  if (!status.enabled) return null;

  const product = h("textarea", { id: "ai-product", maxLength: 2000, rows: 3, placeholder: t("ai.productPlaceholder") });
  const audience = h("input", { id: "ai-audience", maxLength: 300, placeholder: t("ai.defaultAudience") });
  // Язык объявлений — по умолчанию язык кабинета; можно писать рекламу для другого рынка
  const adLanguage = h("select", { id: "ai-language", style: "width:auto" },
    LOCALES.map((code) => h("option", { value: code, lang: code, selected: code === locale }, LOCALE_NAMES[code])));
  const generate = h("button", { id: "ai-generate", type: "button", class: "primary" }, t("ai.generate"));
  const statusBox = h("div", { class: "stack" });
  const results = h("div", { class: "stack" });
  const panel = h("div", { class: "card stack", id: "ai-copywriter" },
    h("h2", { style: "margin:0" }, t("ai.title")),
    h("div", { class: "hint" }, t("ai.hint", { amount: money(status.hold_amount) })),
    h("div", { class: "form-grid" },
      h("div", { class: "field full" }, h("label", { for: "ai-product" }, t("ai.product")), product),
      h("div", { class: "field" }, h("label", { for: "ai-audience" }, t("ai.audience")), audience),
      h("div", { class: "field" }, h("label", { for: "ai-language" }, t("ai.adLanguage")), adLanguage)),
    h("div", { class: "row" }, generate), statusBox, results);

  const showVariants = (variants, language) => results.replaceChildren(...variants.map((v, i) => {
    const use = h("button", { type: "button", class: "small" }, t("ai.use"));
    use.onclick = () => { onUse(v); toast(t("ai.used")); };
    return h("div", { class: "card stack ai-variant", lang: language },
      h("div", { class: "small muted" }, t("ai.variant", { n: i + 1 })),
      h("b", {}, v.title), h("div", {}, v.text),
      h("div", { class: "small muted" }, t("ai.cta", { cta: v.cta })),
      h("div", { class: "row" }, use));
  }));

  generate.onclick = async () => {
    const description = product.value.trim();
    if (description.length < 10) { product.focus(); toast(t("ai.tooShort"), "error"); return; }
    results.replaceChildren();
    const language = adLanguage.value;
    const task = await run(generate, () => api("POST", "/ai/generate-async", {
      product_description: description,
      target_audience: audience.value.trim() || t("ai.defaultAudience"),
      language,
    }));
    if (!task) return;  // 402 / 422 / 429 / 503 — сообщение уже показано
    refreshMe().catch(() => {});  // заморозка видна в балансе сразу
    generate.disabled = true;
    const started = Date.now();
    const progress = h("progress", { style: "width:100%" });  // без value — бегущая полоса
    const label = h("div", { class: "small muted" }, t("ai.queued"));
    statusBox.replaceChildren(progress, label);
    try {
      let delay = 1000;
      while (panel.isConnected) {  // ушли со страницы — опрос прекращается
        await sleep(delay);
        delay = Math.min(delay * 1.5, 4000);
        const job = await api("GET", `/ai/tasks/${task.task_id}`);
        const seconds = Math.round((Date.now() - started) / 1000);
        if (job.status === "completed") {
          statusBox.replaceChildren(h("div", { class: "notice ok" }, t("ai.done")));
          showVariants(job.result.variants, language);
          return;
        }
        if (job.status === "failed") {
          statusBox.replaceChildren(h("div", { class: "notice error" }, job.error || t("ai.failed")));
          return;
        }
        label.textContent = t(job.status === "pending" ? "ai.waiting" : "ai.generating", { seconds });
        if (Date.now() - started > AI_POLL_LIMIT_MS) {
          statusBox.replaceChildren(h("div", { class: "notice warn" }, t("ai.timeout")));
          return;
        }
      }
    } catch (err) {
      statusBox.replaceChildren(h("div", { class: "notice error" }, t("ai.resultError", { error: err.message })));
    } finally {
      if (generate.isConnected) generate.disabled = false;
      refreshMe().catch(() => {});  // списано по факту или возвращено
    }
  };
  return panel;
}

async function campaignFormView(id) {
  const [placements, original] = await Promise.all([
    loadPlacements(true), id ? api("GET", `/campaigns/${id}`) : Promise.resolve(null)]);
  if (!placements.length) {
    return h("div", { class: "notice warn" }, t("form.noPlacements"));
  }
  const c = original || {};
  const f = {
    // Пустое значение — вся сеть (по умолчанию для новой кампании: больше показов)
    placement_id: h("select", { id: "f-placement" },
      h("option", { value: "", selected: c.placement_id == null }, t("form.networkOption")),
      placements.map((p) => h("option", { value: p.id, selected: p.id === c.placement_id },
        t("form.placementOption", { name: p.name, price: money(p.price_per_click) }))),
      // Текущая площадка отключена: оставляем её выбранной, чтобы не сменить молча
      original && original.placement_id != null && !placements.some((p) => p.id === original.placement_id)
        ? h("option", { value: original.placement_id, selected: true }, t("common.disabledRef", { id: original.placement_id }))
        : null),
    cpc_bid: h("input", { id: "f-bid", type: "number", min: "0.01", max: "1000", step: "0.01",
      placeholder: t("form.bidPlaceholder"), value: c.cpc_bid ?? "" }),
    title: h("input", { id: "f-title", required: true, maxLength: 255, value: c.title || "" }),
    description: h("textarea", { id: "f-description", maxLength: 1000 }, c.description || ""),
    image_url: h("input", { id: "f-image", type: "url", placeholder: "https://…", value: c.image_url || "" }),
    target_url: h("input", { id: "f-target", type: "url", required: true, placeholder: "https://…", value: c.target_url || "" }),
    start_date: h("input", { id: "f-start", type: "datetime-local", value: toLocalInput(c.start_date) }),
    end_date: h("input", { id: "f-end", type: "datetime-local", value: toLocalInput(c.end_date) }),
  };
  const values = () => ({
    placement_id: f.placement_id.value ? Number(f.placement_id.value) : null,
    cpc_bid: f.cpc_bid.value ? Number(f.cpc_bid.value) : null,
    title: f.title.value.trim(),
    description: f.description.value.trim() || null,
    image_url: f.image_url.value.trim() || null,
    target_url: f.target_url.value.trim(),
    start_date: fromLocalInput(f.start_date.value),
    end_date: fromLocalInput(f.end_date.value),
  });
  const preview = h("div");
  const updatePreview = () => {
    const v = values();
    preview.replaceChildren(adPreview({ title: v.title || t("form.previewTitle"), description: v.description, image_url: v.image_url }));
  };
  [f.title, f.description, f.image_url].forEach((el) => el.addEventListener("input", updatePreview));
  updatePreview();

  const approved = original && ["active", "paused"].includes(original.status);
  const submit = h("button", { class: "primary", type: "submit" }, id ? t("common.save") : t("form.createDraft"));
  const form = h("form", {
    class: "card",
    onsubmit: async (e) => {
      e.preventDefault();
      const v = values();
      let body = v;
      if (original) {
        // PATCH: отправляем только изменённые поля
        body = {};
        for (const [k, val] of Object.entries(v)) {
          const old = k.endsWith("_date") ? (original[k] ? new Date(original[k]).toISOString() : null) : original[k] ?? null;
          if (val !== old) body[k] = val;
        }
        if (!Object.keys(body).length) { location.hash = `#/campaigns/${id}`; return; }
        const content = ["placement_id", "title", "description", "image_url", "target_url"].some((k) => k in body);
        if (approved && content && !confirm(t("form.remoderateConfirm"))) return;
      }
      const saved = await run(submit, () => api(original ? "PATCH" : "POST", original ? `/campaigns/${id}` : "/campaigns", body),
        original ? t("form.saved") : t("form.draftCreated"));
      if (saved) location.hash = `#/campaigns/${saved.id}`;
    },
  },
  approved ? h("div", { class: "notice info" }, t("form.approvedNotice")) : null,
  h("div", { class: "form-grid" },
    h("div", { class: "field" }, h("label", { for: "f-placement" }, t("form.placement")), f.placement_id),
    h("div", { class: "field" }, h("label", { for: "f-bid" }, t("form.bid")), f.cpc_bid,
      h("div", { class: "hint" }, t("form.bidHint"))),
    h("div", { class: "field full" }, h("label", { for: "f-title" }, t("form.title")), f.title),
    h("div", { class: "field full" }, h("label", { for: "f-description" }, t("form.description")), f.description,
      h("div", { class: "hint" }, t("form.descriptionHint"))),
    h("div", { class: "field" }, h("label", { for: "f-image" }, t("form.image")), f.image_url,
      h("div", { class: "hint" }, t("form.imageHint"))),
    h("div", { class: "field" }, h("label", { for: "f-target" }, t("form.target")), f.target_url),
    h("div", { class: "field" }, h("label", { for: "f-start" }, t("form.start")), f.start_date,
      h("div", { class: "hint" }, t("form.startHint"))),
    h("div", { class: "field" }, h("label", { for: "f-end" }, t("form.end")), f.end_date,
      h("div", { class: "hint" }, t("form.endHint")))),
  h("div", { class: "field" }, h("label", {}, t("form.preview")), preview),
  h("div", { class: "row" }, submit,
    h("a", { class: "btn", href: id ? `#/campaigns/${id}` : "#/campaigns" }, t("common.cancel"))));

  // Выбранный вариант AI-копирайтера — в поля формы (в пределах их длины); сохраняет пользователь сам
  const aiPanel = await aiCopywriterPanel((v) => {
    f.title.value = v.title.slice(0, 255);
    f.description.value = `${v.text} ${v.cta}`.trim().slice(0, 1000);
    updatePreview();
    f.title.focus();
  });

  return h("div", {}, h("h1", {}, id ? t("form.editTitle") : t("form.newTitle")), aiPanel, form);
}

// ---------- Кошелёк ----------
async function walletView() {
  await refreshMe();
  const rows = h("tbody");
  const txRow = (op) => h("tr", {},
      h("td", { class: "nowrap" }, dateTime(op.created_at)),
      h("td", {}, known("tx", op.type)),
      h("td", {}, op.description || "",
        op.campaign_id ? h("span", {}, " · ",
          h("a", { href: `#/campaigns/${op.campaign_id}` }, t("wallet.campaignRef", { id: op.campaign_id }))) : null),
      h("td", { class: "num", style: TX_SPEND.has(op.type) ? "" : "color:var(--ok)" },
        (TX_SPEND.has(op.type) ? "−" : "+") + money(op.amount)));
  const { more, first: count } = await cursorFeed("/wallet/history", 50, txRow, rows);

  const isAdmin = state.me.role === "admin";
  const amount = h("input", { type: "number", min: "0.01", max: "1000000", step: "0.01", placeholder: t("common.amount"), style: "width:140px" });
  const depositBtn = h("button", { class: "primary", type: "submit" }, t("wallet.topUp"));
  const depositForm = isAdmin ? h("form", {
    class: "row",
    onsubmit: async (e) => {
      e.preventDefault();
      const ok = await run(depositBtn, () => api("POST", "/wallet/deposit", { amount: amount.value }), t("wallet.deposited"));
      if (ok) document.getElementById("app") && render();
    },
  }, amount, depositBtn) : h("p", { class: "muted small" }, t("wallet.adminOnly"));

  const payCfg = await api("GET", "/payments/config").catch(() => ({ enabled: false }));
  return h("div", {},
    h("h1", {}, t("wallet.title")),
    h("div", { class: "grid" }, statCard(t("stat.balance"), money(state.me.balance)),
      // Резерв под AI-генерации, которые ещё выполняются: после них вернётся или спишется по факту
      Number(state.me.held_balance) > 0 ? statCard(t("stat.held"), money(state.me.held_balance)) : null),
    h("div", { class: "card stack" }, h("h2", {}, t("wallet.topUpTitle")),
      payCfg.enabled ? topUpForm(payCfg) : null,
      // Админ пополняет вручную всегда; рекламодатель — подсказка, только если оплаты картой нет
      isAdmin || !payCfg.enabled ? depositForm : null),
    await plansCard(payCfg),
    h("div", { class: "card" }, h("h2", {}, t("wallet.history")),
      count ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, t("col.when")), h("th", {}, t("col.type")), h("th", {}, t("col.description")),
          h("th", { class: "num" }, t("col.amount")))),
        rows)) : h("div", { class: "empty" }, t("wallet.noOperations")),
      h("div", { style: "margin-top:10px" }, more)));
}

// ---------- Оплата картой и тарифы ----------
// Перейти на страницу оплаты: только свой домен или https (адрес приходит от нашего сервера,
// но ссылку javascript:… или http-страницу не открываем ни при каких условиях)
function goToPayment(url) {
  if (typeof url === "string" && (/^\/(?!\/)/.test(url) || /^https:\/\//.test(url))) {
    location.href = url;
    return true;
  }
  toast(t("pay.badUrl"), "error");
  return false;
}

function topUpForm(cfg) {
  const amount = h("input", { type: "number", id: "pay-amount", min: String(cfg.min_amount), max: String(cfg.max_amount),
    step: "0.01", placeholder: t("common.amount"), required: true, style: "width:140px" });
  const pay = h("button", { class: "primary", type: "submit" }, t("pay.card"));
  return h("div", { class: "stack" },
    cfg.test_mode ? h("div", { class: "notice warn" }, t("pay.testMode")) : null,
    h("form", {
      class: "row",
      onsubmit: async (e) => {
        e.preventDefault();
        const p = await run(pay, () => api("POST", "/payments/top-up", { amount: amount.value }));
        if (p) goToPayment(p.confirmation_url);
      },
    }, amount, h("span", { class: "muted small" }, cfg.currency), pay),
    h("div", { class: "small muted" }, t("pay.range", { min: money(cfg.min_amount), max: money(cfg.max_amount) })));
}

async function plansCard(payCfg) {
  const [plans, my] = await Promise.all([
    api("GET", "/plans").catch(() => []),
    api("GET", "/plans/my").catch(() => ({ subscription: null })),
  ]);
  if (!plans.length && !my.subscription) return null;  // тарифов нет — блока нет
  const current = my.subscription
    ? h("div", { class: "notice ok" }, tx("plans.current", { name: h("b", {}, my.plan ? my.plan.name : "—") }),
      my.subscription.ends_at ? t("plans.until", { date: dateTime(my.subscription.ends_at) }) : t("plans.forever"))
    : h("div", { class: "muted small" }, t("plans.none"));
  const list = plans.map((p) => {
    const buy = h("button", { class: Number(p.price) > 0 ? "primary small" : "small" },
      Number(p.price) > 0 ? t("plans.buy", { price: money(p.price) }) : t("plans.free"));
    buy.disabled = Number(p.price) > 0 && !payCfg.enabled;  // платный — только при подключённой оплате
    buy.onclick = async () => {
      const r = await run(buy, () => api("POST", `/plans/${encodeURIComponent(p.code)}/buy`));
      if (!r) return;
      if (r.payment) goToPayment(r.payment.confirmation_url);
      else { toast(t("plans.connected")); render(); }
    };
    return h("div", { class: "card stack plan-item" },
      h("div", { class: "row between" }, h("b", {}, p.name),
        h("span", { class: "muted small" }, p.period_days ? t("plans.period", { n: p.period_days }) : t("plans.once"))),
      p.description ? h("div", { class: "small" }, p.description) : null,
      h("div", { class: "row" }, buy));
  });
  return h("div", { class: "card stack", id: "plans-card" }, h("h2", {}, t("plans.title")), current,
    list.length ? h("div", { class: "grid" }, list) : null,
    plans.some((p) => Number(p.price) > 0) && !payCfg.enabled
      ? h("div", { class: "small muted" }, t("plans.cardUnavailable")) : null);
}

// ---------- Админ: модерация ----------
// Подсказка AI-проверки в карточке модерации. Решение всё равно за модератором
// Вид плашки по вердикту; текст — moderation.ai.<вердикт>, уровень риска — moderation.ai.risk.<уровень>
const AI_VERDICT_KIND = { approve: "ok", review: "warn", reject: "error", error: "info" };

function aiHint(c, aiEnabled, onRecheck) {
  if (!aiEnabled && !c.ai_verdict) return null;
  const recheck = aiEnabled
    ? h("button", { class: "small" }, t(c.ai_verdict ? "moderation.ai.recheck" : "moderation.ai.check")) : null;
  if (recheck) recheck.onclick = async () => {
    if (await run(recheck, () => api("POST", `/campaigns/${c.id}/ai-review`), t("moderation.ai.checked"))) onRecheck();
  };
  if (!c.ai_verdict) {
    return h("div", { class: "notice info row between" },
      h("span", {}, t("moderation.ai.pending")), recheck);
  }
  const kind = AI_VERDICT_KIND[c.ai_verdict] || "info";
  const label = AI_VERDICT_KIND[c.ai_verdict]
    ? t(`moderation.ai.${c.ai_verdict}`) : t("moderation.ai.unknown", { verdict: c.ai_verdict });
  return h("div", { class: `notice ${kind} stack` },
    h("div", { class: "row between" },
      h("b", {}, label, c.ai_risk ? t("moderation.ai.risk", { risk: known("moderation.ai.risk", c.ai_risk) }) : ""),
      h("span", { class: "small" }, c.ai_checked_at ? dateTime(c.ai_checked_at) : "", " ", recheck)),
    c.ai_summary ? h("div", {}, c.ai_summary) : null,
    c.ai_reasons && c.ai_reasons.length ? h("ul", { class: "small", style: "margin:0;padding-left:18px" },
      c.ai_reasons.map((r) => h("li", {}, r))) : null);
}

async function moderationView() {
  const [page, aiStatus] = await Promise.all([
    api("GET", "/campaigns?status=moderation&limit=200"),
    api("GET", "/campaigns/ai-status").catch(() => ({ enabled: false })),  // старый сервер — без AI
  ]);
  const queue = page.items;
  const wrap = h("div");
  const reload = async () => wrap.replaceWith(await moderationView());
  add(wrap, h("h1", {}, t("moderation.title"), " ",
    page.total ? h("span", { class: "badge count" }, page.total) : null),
  page.total > queue.length ? h("div", { class: "notice info" },
    t("moderation.partial", { shown: queue.length, total: page.total })) : null);
  if (!queue.length) {
    add(wrap, h("div", { class: "card empty" }, t("moderation.empty")));
    return wrap;
  }
  queue.forEach((c) => {
    const reason = h("input", { placeholder: t("moderation.reasonPlaceholder"), maxLength: 1000 });
    const approve = h("button", { class: "primary small" }, t("moderation.approve"));
    const reject = h("button", { class: "danger small" }, t("moderation.reject"));
    approve.onclick = async () => {
      if (await run(approve, () => api("PATCH", `/campaigns/${c.id}/moderate`, { status: "active" }), t("moderation.approved"))) reload();
    };
    reject.onclick = async () => {
      if (!reason.value.trim()) { reason.focus(); toast(t("moderation.reasonRequired"), "error"); return; }
      const body = { status: "rejected", rejection_reason: reason.value.trim() };
      if (await run(reject, () => api("PATCH", `/campaigns/${c.id}/moderate`, body), t("moderation.rejected"))) reload();
    };
    add(wrap, h("div", { class: "card stack" },
      h("div", { class: "row between" },
        h("div", {}, h("b", {}, `#${c.id} `), c.title),
        h("span", { class: "small muted" }, c.owner_email, " · ", c.placement_name || t("campaign.network"), " · ",
          dateTime(c.created_at))),
      adPreview(c),
      aiHint(c, aiStatus.enabled, reload),
      h("div", { class: "small" }, tx("moderation.link", {
        url: h("a", { href: c.target_url, target: "_blank", rel: "noopener noreferrer", class: "break" }, c.target_url) })),
      c.image_url ? h("div", { class: "small muted break" }, t("moderation.image", { url: c.image_url })) : null,
      h("div", { class: "row" }, approve, h("div", { style: "flex:1;min-width:200px" }, reason), reject)));
  });
  return wrap;
}

// ---------- Админ: площадки ----------
function embedCode(code) {
  return `<script async src="${location.origin}/widget.js" data-placement="${code}"></script>`;
}

async function placementsView() {
  const list = (await api("GET", "/placements/all?limit=500")).items;
  const wrap = h("div");
  const reload = async () => { state.placements = null; wrap.replaceWith(await placementsView()); };

  const nf = {
    name: h("input", { required: true, maxLength: 255, placeholder: t("placements.namePlaceholder") }),
    code: h("input", { required: true, maxLength: 100, pattern: "[A-Za-z0-9_-]+", placeholder: "header_banner" }),
    ppc: h("input", { type: "number", min: "0", step: "0.01", value: "0" }),
    ppd: h("input", { type: "number", min: "0", step: "0.01", value: "0" }),
  };
  const createBtn = h("button", { class: "primary", type: "submit" }, t("placements.create"));
  const createForm = h("form", {
    class: "card",
    onsubmit: async (e) => {
      e.preventDefault();
      const body = { name: nf.name.value.trim(), code_identifier: nf.code.value.trim(),
        price_per_click: nf.ppc.value || "0", price_per_day: nf.ppd.value || "0" };
      if (await run(createBtn, () => api("POST", "/placements", body), t("placements.created"))) reload();
    },
  },
  h("h2", {}, t("placements.newTitle")),
  h("div", { class: "form-grid" },
    h("div", { class: "field" }, h("label", {}, t("placements.name")), nf.name),
    h("div", { class: "field" }, h("label", {}, t("placements.code")), nf.code,
      h("div", { class: "hint" }, t("placements.codeHint"))),
    h("div", { class: "field" }, h("label", {}, t("placements.pricePerClick")), nf.ppc),
    h("div", { class: "field" }, h("label", {}, t("placements.pricePerDay")), nf.ppd,
      h("div", { class: "hint" }, t("placements.pricePerDayHint")))),
  createBtn);

  const rows = list.map((p) => {
    const price = h("input", { type: "number", min: "0", step: "0.01", value: p.price_per_click });
    const save = h("button", { class: "small" }, t("common.save"));
    save.onclick = async () => {
      if (await run(save, () => api("PATCH", `/placements/${p.id}`, { price_per_click: price.value }), t("placements.priceUpdated"))) reload();
    };
    const toggle = h("button", { class: "small" + (p.is_active ? " danger" : "") }, t(p.is_active ? "placements.disable" : "placements.enable"));
    toggle.onclick = async () => {
      if (p.is_active && !confirm(t("placements.disableConfirm", { name: p.name }))) return;
      if (await run(toggle, () => api("PATCH", `/placements/${p.id}`, { is_active: !p.is_active }))) reload();
    };
    const snippet = embedCode(p.code_identifier);
    return h("tr", {},
      h("td", {}, h("b", {}, p.name), h("div", { class: "mono muted" }, p.code_identifier),
        p.is_active ? null : h("span", { class: "badge" }, t("placements.disabled"))),
      h("td", {}, h("div", { class: "inline-form" }, price, save)),
      h("td", {},
        h("pre", { class: "code mono" }, snippet),
        h("div", { class: "row" },
          h("button", { class: "small", onclick: () => copy(snippet) }, t("placements.copyCode")),
          h("a", { class: "btn small", href: `/demo?placement=${encodeURIComponent(p.code_identifier)}`, target: "_blank" }, t("placements.demo")))),
      h("td", {}, toggle));
  });

  add(wrap, h("h1", {}, t("placements.title")), createForm,
    h("div", { class: "card" },
      table([t("col.placement"), t("col.pricePerClick"), t("col.embed"), ""], rows, t("placements.empty"))));
  return wrap;
}

// ---------- Админ: пользователи ----------
async function usersView(query = "") {
  const page = await api("GET", `/users?limit=200${query ? "&q=" + encodeURIComponent(query) : ""}`, undefined, { page: true });
  const list = page.items;
  const wrap = h("div");
  const reload = async () => wrap.replaceWith(await usersView(search.value.trim()));
  const search = h("input", { type: "search", placeholder: t("users.search"), value: query, style: "max-width:320px" });
  const searchForm = h("form", { class: "row", onsubmit: (e) => { e.preventDefault(); reload(); } },
    search, h("button", { type: "submit" }, t("users.find")));

  const rows = list.map((u) => {
    const amount = h("input", { type: "number", min: "0.01", step: "0.01", placeholder: t("common.amount") });
    const deposit = h("button", { class: "small primary" }, t("users.deposit"));
    deposit.onclick = async () => {
      if (!amount.value) { amount.focus(); return; }
      const res = await run(deposit, () => api("POST", `/wallet/deposit?user_id=${u.id}`, { amount: amount.value }),
        t("users.deposited", { email: u.email }));
      if (res) { if (u.id === state.me.id) await refreshMe(); reload(); }
    };
    const self = u.id === state.me.id;
    const roleBtn = h("button", { class: "small", disabled: self, title: self ? t("users.selfRole") : "" },
      t(u.role === "admin" ? "users.makeAdvertiser" : "users.makeAdmin"));
    roleBtn.onclick = async () => {
      const role = u.role === "admin" ? "advertiser" : "admin";
      if (!confirm(t("users.roleConfirm", { email: u.email, role: t(`role.${role}`) }))) return;
      if (await run(roleBtn, () => api("PATCH", `/users/${u.id}/role`, { role }), t("users.roleChanged"))) reload();
    };
    return h("tr", {},
      h("td", {}, u.email, h("div", { class: "small muted" }, t("users.since", { id: u.id, date: dateTime(u.created_at) }))),
      h("td", {}, u.role === "admin" ? h("span", { class: "badge paused" }, t("role.admin")) : t("role.advertiser")),
      h("td", { class: "num" }, money(u.balance)),
      h("td", {}, h("div", { class: "inline-form" }, amount, deposit)),
      h("td", {}, roleBtn));
  });
  add(wrap, h("h1", {}, t("users.title")),
    h("div", { class: "card" }, searchForm,
      page.hasMore ? h("p", { class: "small muted", style: "margin:8px 0 0" }, t("users.hasMore")) : null),
    h("div", { class: "card" },
      table([t("col.user"), t("col.role"), { label: t("col.balance"), num: 1 }, t("col.topUp"), ""], rows,
        t(query ? "users.notFound" : "users.none"))));
  return wrap;
}

// ---------- Админ: платформа ----------
async function platformView(days = 30) {
  const s = await api("GET", `/stats/platform?days=${days}`);
  const wrap = h("div");
  const reload = async (n) => wrap.replaceWith(await platformView(n));
  add(wrap,
    h("div", { class: "row between" }, h("h1", {}, t("platform.title")), daysSelect(days, reload)),
    h("div", { class: "grid" },
      statCard(t("stat.revenue"), money(s.totals.spend)),
      statCard(t("stat.impressions"), int(s.totals.impressions)),
      statCard(t("stat.clicks"), int(s.totals.clicks)),
      statCard(t("stat.ctr"), ctr(s.totals)),
      statCard(t("stat.users"), int(s.users_count)),
      statCard(t("stat.activeCampaigns"), int(s.active_campaigns)),
      statCard(t("stat.moderation"), int(s.moderation_queue)),
      statCard(t("stat.advertisersBalance"), money(s.advertisers_balance))),
    h("div", { class: "card" }, h("h2", {}, t("common.byDays")), chart(s.days)),
    h("div", { class: "card" }, h("h2", {}, t("platform.byPlacements")),
      table([t("col.placement"), { label: t("col.impressions"), num: 1 }, { label: t("col.clicks"), num: 1 },
        { label: t("col.ctr"), num: 1 }, { label: t("col.revenue"), num: 1 }],
        s.placements.map((p) => h("tr", {},
          h("td", {}, p.name, " ", h("span", { class: "mono muted" }, p.code_identifier),
            p.is_active ? null : h("span", { class: "badge" }, t("platform.disabled"))),
          h("td", { class: "num" }, int(p.impressions)), h("td", { class: "num" }, int(p.clicks)),
          h("td", { class: "num" }, ctr(p)), h("td", { class: "num" }, money(p.spend)))),
        t("platform.noPlacements"))));
  return wrap;
}

// ---------- Профиль: смена пароля ----------
async function profileView() {
  const cur = h("input", { type: "password", id: "p-current", required: true, autocomplete: "current-password" });
  const next = h("input", { type: "password", id: "p-new", required: true, minLength: 8, autocomplete: "new-password" });
  const again = h("input", { type: "password", id: "p-again", required: true, minLength: 8, autocomplete: "new-password" });
  const submit = h("button", { class: "primary", type: "submit" }, t("profile.change"));
  const form = h("form", {
    class: "card",
    style: "max-width:420px",
    onsubmit: async (e) => {
      e.preventDefault();
      if (next.value !== again.value) { toast(t("profile.mismatch"), "error"); again.focus(); return; }
      const res = await run(submit, () => api("POST", "/auth/change-password",
        { current_password: cur.value, new_password: next.value }),
      t("profile.changed"));
      if (res) { writeToken(res.access_token); form.reset(); }
    },
  },
  h("h2", {}, t("profile.changeTitle")),
  h("div", { class: "field" }, h("label", { for: "p-current" }, t("profile.current")), cur),
  h("div", { class: "field" }, h("label", { for: "p-new" }, t("profile.new")), next,
    h("div", { class: "hint" }, t("profile.newHint"))),
  h("div", { class: "field" }, h("label", { for: "p-again" }, t("profile.again")), again),
  submit);
  return h("div", {},
    h("h1", {}, t("profile.title")),
    h("div", { class: "card small" }, h("div", {}, tx("profile.email", { email: h("b", {}, state.me.email) })),
      h("div", {}, t("profile.role", { role: t(state.me.role === "admin" ? "profile.roleAdmin" : "role.advertiser") })),
      h("div", { class: "muted" }, t("profile.registered", { date: dateTime(state.me.created_at) }))),
    await telegramCard(),
    form);
}

// Привязка Telegram-бота: одноразовый код из кабинета → бот. Пароль боту не нужен
async function telegramCard() {
  const status = await api("GET", "/telegram/status").catch(() => ({ enabled: false }));
  if (!status.enabled) return null;
  const card = h("div", { class: "card stack", id: "telegram-card", style: "max-width:520px" });
  const reload = async () => card.replaceWith(await telegramCard());
  add(card, h("h2", { style: "margin:0" }, "Telegram"));

  if (status.linked) {
    const unlink = h("button", { class: "danger small" }, t("telegram.unlink"));
    unlink.onclick = async () => {
      // 204 без тела: api() вернёт null — успех отмечаем явно
      const ok = await run(unlink, async () => { await api("DELETE", "/telegram/link"); return true; }, t("telegram.unlinked"));
      if (ok) reload();
    };
    add(card, h("div", { class: "notice ok" }, t("telegram.linked")),
      h("div", { class: "row" }, unlink));
    return card;
  }

  const getCode = h("button", { class: "primary small" }, t("telegram.getCode"));
  const out = h("div", { class: "stack" });
  getCode.onclick = async () => {
    const res = await run(getCode, () => api("POST", "/telegram/link-code"));
    if (!res) return;
    out.replaceChildren(
      h("div", {}, tx("telegram.code", { code: h("b", { style: "font-size:20px;letter-spacing:2px" }, res.code) }),
        h("span", { class: "small muted" }, t("telegram.validUntil", { date: dateTime(res.expires_at) }))),
      res.deep_link
        ? h("div", {}, h("a", { href: res.deep_link, target: "_blank", rel: "noopener noreferrer", class: "btn primary" },
          t("telegram.open")))
        : h("div", { class: "small muted" }, t("telegram.send", { code: res.code })),
      h("div", { class: "small muted" }, t("telegram.reload")));
  };
  add(card,
    h("div", { class: "small muted" }, status.bot_username ? `${t("telegram.bot", { name: status.bot_username })} ` : "",
      t("telegram.lead")),
    h("div", { class: "row" }, getCode), out);
  return card;
}

// ---------- Старт ----------
window.addEventListener("hashchange", render);
render();
