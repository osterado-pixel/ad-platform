"use strict";
/*
 * Ad Platform — веб-интерфейс (без сборки, без зависимостей).
 * Все данные выводятся через textContent (функция h), innerHTML не используется:
 * текст рекламодателей и пользователей не может выполниться как код.
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
  const headers = {};
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
    throw new ApiError(0, "Сервер недоступен. Проверьте, что он запущен.");
  }
  if (response.status === 204) return opts.page ? { items: [], hasMore: false, next: null } : null;
  let data = null;
  try { data = await response.json(); } catch { /* пустой ответ */ }
  if (!response.ok) {
    if (response.status === 401 && state.token && !opts.keepSession) {
      logout("Сессия истекла — войдите снова");
    }
    throw new ApiError(response.status, errorText(data) || `Ошибка ${response.status}`);
  }
  if (opts.page) {
    // Список постранично: X-Has-More — есть ли ещё, X-Next-Before-Id — курсор следующей страницы
    return {
      items: data,
      hasMore: response.headers.get("X-Has-More") === "true",
      next: response.headers.get("X-Next-Before-Id"),
    };
  }
  return data;
}

// Лента с подгрузкой по курсору: возвращает кнопку «Показать ещё» и первую страницу
async function cursorFeed(path, limit, renderRow, tbody) {
  let cursor = null;
  const more = h("button", { class: "small" }, "Показать ещё");
  const load = async () => {
    const sep = path.includes("?") ? "&" : "?";
    const page = await api("GET", `${path}${sep}limit=${limit}${cursor ? "&before_id=" + cursor : ""}`, undefined, { page: true });
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
const STATUS = {
  draft: "Черновик", moderation: "На модерации", ready_to_pay: "Ожидает оплаты", active: "Активна",
  paused: "На паузе", completed: "Завершена", rejected: "Отклонена",
};
const TX_TYPE = { deposit: "Пополнение", click_spend: "Оплата клика", refund: "Возврат" };

const money = (v) => Number(v || 0).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const int = (v) => Number(v || 0).toLocaleString("ru-RU");
// CTR по объекту статистики: без показов — прочерк (API в этом случае отдаёт 0)
const ctr = (t) => (t && t.impressions ? `${t.ctr.toLocaleString("ru-RU")}%` : "—");
const statusBadge = (s) => h("span", { class: `badge ${s}` }, STATUS[s] || s);

function dateTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}
function shortDay(isoDay) {
  const [, m, d] = isoDay.split("-");
  return `${d}.${m}`;
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
  if (!rows.length) return h("div", { class: "empty" }, emptyText || "Пусто");
  return h("div", { class: "table-wrap" },
    h("table", {},
      h("thead", {}, h("tr", {}, headers.map((x) => h("th", { class: x.num ? "num" : "" }, x.label || x)))),
      h("tbody", {}, rows)));
}

// Столбчатый график: показы (светлые) и клики (яркие) по дням
function chart(days) {
  if (!days.length) return h("div", { class: "empty" }, "Нет данных");
  const W = 720, H = 200, top = 10, bottom = 22, left = 34;
  // Целые деления шкалы: при 3 показах — 0,1,2,3,4, а не 0,1,2,2,3 после округления
  const maxImp = Math.ceil(Math.max(1, ...days.map((d) => d.impressions)) / 4) * 4;
  const maxClk = Math.max(1, ...days.map((d) => d.clicks));
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "chart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Показы и клики по дням");
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
    const tip = `${shortDay(d.day)}: показов ${int(d.impressions)}, кликов ${int(d.clicks)}, расход ${money(d.spend)}`;
    s("rect", { x: x - bar, y: top + plotH - hi, width: bar, height: hi, class: "a" }).append(
      Object.assign(document.createElementNS(ns, "title"), { textContent: tip }));
    s("rect", { x: x, y: top + plotH - hc, width: bar, height: hc, class: "b" }).append(
      Object.assign(document.createElementNS(ns, "title"), { textContent: tip }));
    if (i % labelEvery === 0) s("text", { x, y: H - 6, "text-anchor": "middle", class: "axis" }, shortDay(d.day));
  });
  return h("div", {},
    h("div", { class: "legend" },
      h("span", {}, h("i", { style: "background:var(--chart-a)" }), "Показы (шкала слева)"),
      h("span", {}, h("i", { style: "background:var(--chart-b)" }), "Клики (своя шкала)")),
    svg);
}

function daysSelect(current, onChange) {
  return h("select", { style: "width:auto", onchange: (e) => onChange(Number(e.target.value)) },
    [7, 30, 90].map((n) => h("option", { value: n, selected: n === current }, `${n} дней`)));
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
  try { await navigator.clipboard.writeText(text); toast("Скопировано"); }
  catch { toast("Не удалось скопировать — выделите текст вручную", "error"); }
}

// ---------- Вход и регистрация ----------
function authView() {
  let mode = "login";
  const error = h("div");
  const email = h("input", { type: "email", required: true, autocomplete: "email", id: "auth-email" });
  const password = h("input", { type: "password", required: true, minLength: 8, id: "auth-password",
    autocomplete: "current-password" });
  const submit = h("button", { class: "primary", type: "submit", style: "width:100%;justify-content:center" }, "Войти");
  const tabs = h("div", { class: "tabs" });
  const setMode = (m) => {
    mode = m;
    submit.textContent = m === "login" ? "Войти" : "Зарегистрироваться";
    password.autocomplete = m === "login" ? "current-password" : "new-password";
    tabs.replaceChildren(
      h("button", { type: "button", class: m === "login" ? "on" : "", onclick: () => setMode("login") }, "Вход"),
      h("button", { type: "button", class: m === "register" ? "on" : "", onclick: () => setMode("register") }, "Регистрация"));
    error.replaceChildren();
  };
  setMode("login");

  const form = h("form", {
    class: "card",
    onsubmit: async (e) => {
      e.preventDefault();
      error.replaceChildren();
      submit.disabled = true;
      try {
        if (mode === "register") {
          await api("POST", "/auth/register", { email: email.value, password: password.value });
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
  h("div", { class: "field" }, h("label", { for: "auth-email" }, "Email"), email),
  h("div", { class: "field" }, h("label", { for: "auth-password" }, "Пароль"), password,
    h("div", { class: "hint" }, "Не короче 8 символов")),
  submit);

  return h("div", { class: "auth" }, h("span", { class: "brand" }, "Ad", h("span", {}, "Platform")), form);
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
  { path: "#/overview", title: "Обзор", view: overviewView },
  { path: "#/campaigns", title: "Кампании", view: campaignsView },
  { path: "#/wallet", title: "Кошелёк", view: walletView },
  { path: "#/admin/moderation", title: "Модерация", view: moderationView, admin: true },
  { path: "#/admin/placements", title: "Площадки", view: placementsView, admin: true },
  { path: "#/admin/users", title: "Пользователи", view: usersView, admin: true },
  { path: "#/admin/platform", title: "Платформа", view: platformView, admin: true },
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
      "Не удалось загрузить профиль. Проверьте, что сервер запущен, и обновите страницу.")));
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
  const main = h("main", {}, h("p", { class: "muted" }, "Загрузка…"));
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
      h("div", {}, h("a", { href: "#/profile", title: "Профиль и пароль" }, me.email), me.role === "admin" ? " · админ" : ""),
      h("div", {}, "Баланс: ", h("b", { id: "balance" }, money(me.balance)))),
    h("button", { class: "small", onclick: () => logout() }, "Выйти"));
}

function navbar(active) {
  const nav = h("nav", { class: "nav" });
  ROUTES.forEach((r, i) => {
    if (r.admin && state.me.role !== "admin") return;
    if (r.admin && !ROUTES[i - 1].admin) nav.append(h("span", { class: "sep" }));
    nav.append(h("a", { href: r.path, class: r.path === active ? "active" : "" }, r.title));
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
    h("div", { class: "row between" }, h("h1", {}, "Обзор"), daysSelect(days, reload)),
    lowBalance ? h("div", { class: "notice warn" },
      "Баланс исчерпан — активные кампании не показываются. Пополнение выполняет администратор.") : null,
    h("div", { class: "grid" },
      statCard("Баланс", money(s.balance)),
      statCard("Активных кампаний", int(active)),
      statCard("Показы", int(s.totals.impressions)),
      statCard("Клики", int(s.totals.clicks)),
      statCard("CTR", ctr(s.totals)),
      statCard("Расход", money(s.totals.spend))),
    h("div", { class: "card" }, h("h2", {}, "По дням"), chart(s.days)),
    h("div", { class: "card" },
      h("div", { class: "row between" }, h("h2", {}, "Кампании за период"),
        h("a", { class: "btn small primary", href: "#/campaigns/new" }, "+ Новая кампания")),
      table(["Кампания", "Статус", { label: "Показы", num: 1 }, { label: "Клики", num: 1 },
        { label: "CTR", num: 1 }, { label: "Расход", num: 1 }],
      s.campaigns.map((c) => h("tr", {},
        h("td", {}, h("a", { href: `#/campaigns/${c.campaign_id}` }, c.title)),
        h("td", {}, statusBadge(c.status)),
        h("td", { class: "num" }, int(c.impressions)), h("td", { class: "num" }, int(c.clicks)),
        h("td", { class: "num" }, ctr(c)), h("td", { class: "num" }, money(c.spend)))),
      "Кампаний пока нет — создайте первую"),
      s.campaigns_has_more ? h("p", { class: "small muted" },
        "Показаны последние 50 кампаний. Все — в разделе ", h("a", { href: "#/campaigns" }, "«Кампании»"), ".") : null));
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
  const edit = h("a", { class: "btn small", href: `#/campaigns/${c.id}/edit` }, "Изменить");
  const buttons = [];
  if (c.status === "draft" || c.status === "rejected") {
    buttons.push(act("На модерацию", "POST", `${base}/submit`, { primary: true, done: "Отправлено на модерацию" }), edit);
    if (!c.impressions_count) {
      buttons.push(act("Удалить", "DELETE", base, { danger: true, confirm: `Удалить «${c.title}»?`, done: "Удалено" }));
    }
  }
  if (c.status === "active") {
    buttons.push(act("Пауза", "POST", `${base}/pause`, { done: "Кампания на паузе" }), edit);
  }
  if (c.status === "paused") {
    buttons.push(act("Возобновить", "POST", `${base}/resume`, { primary: true, done: "Кампания возобновлена" }), edit);
  }
  if (!["completed", "draft", "rejected"].includes(c.status)) {
    buttons.push(act("Завершить", "POST", `${base}/complete`, {
      danger: true, done: "Кампания завершена",
      confirm: `Завершить «${c.title}»? Это окончательно: показ прекратится, статистика сохранится.`,
    }));
  }
  return h("div", { class: "actions" }, buttons);
}

async function campaignsView() {
  const placements = await loadPlacements();
  const placeName = (id) => (placements.find((p) => p.id === id) || {}).name || `#${id} (отключена)`;
  const wrap = h("div");
  const reload = async () => wrap.replaceWith(await campaignsView());
  const rows = h("tbody");
  const row = (c) => h("tr", {},
        h("td", {}, h("a", { href: `#/campaigns/${c.id}` }, c.title),
          c.status === "rejected" && c.rejection_reason
            ? h("div", { class: "small", style: "color:var(--danger)" }, "Причина: ", c.rejection_reason) : null),
        h("td", {}, placeName(c.placement_id)),
        h("td", {}, statusBadge(c.status)),
        h("td", { class: "num" }, int(c.impressions_count)),
        h("td", { class: "num" }, int(c.clicks_count)),
        h("td", {}, campaignActions(c, reload)));
  const feed = await cursorFeed("/campaigns/my", 50, row, rows);
  const headers = ["Кампания", "Площадка", "Статус", { label: "Показы", num: 1 }, { label: "Клики", num: 1 }, "Действия"];
  add(wrap,
    h("div", { class: "row between" }, h("h1", {}, "Кампании"),
      h("a", { class: "btn primary", href: "#/campaigns/new" }, "+ Новая кампания")),
    h("div", { class: "card" },
      feed.first ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, headers.map((x) => h("th", { class: x.num ? "num" : "" }, x.label || x)))), rows))
        : h("div", { class: "empty" },
          "Кампаний пока нет. Создайте кампанию, отправьте на модерацию — после одобрения она начнёт показываться."),
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
      ? h("div", { class: "notice error" }, "Отклонена модератором: ", c.rejection_reason) : null,
    c.status === "moderation" ? h("div", { class: "notice warn" }, "Кампания на модерации — показ начнётся после одобрения.") : null,
    h("div", { class: "card stack" },
      adPreview(c),
      h("div", { class: "small muted" },
        "Ведёт на: ", h("a", { href: c.target_url, target: "_blank", rel: "noopener noreferrer", class: "break" }, c.target_url)),
      h("div", { class: "small muted" },
        "Площадка: ", placement ? `${placement.name} (${money(placement.price_per_click)} за клик)` : `#${c.placement_id}`,
        " · Показ: ", c.start_date ? `с ${dateTime(c.start_date)}` : "сразу",
        c.end_date ? ` до ${dateTime(c.end_date)}` : ", бессрочно"),
      campaignActions(c, () => reload())),
    h("div", { class: "grid" },
      statCard("Показы", int(s.totals.impressions)),
      statCard("Клики", int(s.totals.clicks)),
      statCard("CTR", ctr(s.totals)),
      statCard("Расход", money(s.totals.spend)),
      statCard("Всего показов", int(c.impressions_count)),
      statCard("Всего кликов", int(c.clicks_count))),
    h("div", { class: "card" }, h("h2", {}, "По дням"), chart(s.days)),
    h("div", { class: "card" }, table(["День", { label: "Показы", num: 1 }, { label: "Клики", num: 1 },
      { label: "CTR", num: 1 }, { label: "Расход", num: 1 }],
    s.days.slice().reverse().filter((d) => d.impressions || d.clicks).map((d) => h("tr", {},
      h("td", {}, shortDay(d.day)), h("td", { class: "num" }, int(d.impressions)),
      h("td", { class: "num" }, int(d.clicks)), h("td", { class: "num" }, ctr(d)),
      h("td", { class: "num" }, money(d.spend)))),
    "За период событий не было")));
  return wrap;
}

async function campaignFormView(id) {
  const [placements, original] = await Promise.all([
    loadPlacements(true), id ? api("GET", `/campaigns/${id}`) : Promise.resolve(null)]);
  if (!placements.length) {
    return h("div", { class: "notice warn" }, "Нет активных рекламных площадок — обратитесь к администратору.");
  }
  const c = original || {};
  const f = {
    placement_id: h("select", { id: "f-placement", required: true },
      placements.map((p) => h("option", { value: p.id, selected: p.id === c.placement_id },
        `${p.name} — ${money(p.price_per_click)} за клик`)),
      // Текущая площадка отключена: оставляем её выбранной, чтобы не сменить молча
      original && !placements.some((p) => p.id === original.placement_id)
        ? h("option", { value: original.placement_id, selected: true }, `#${original.placement_id} (отключена)`) : null),
    title: h("input", { id: "f-title", required: true, maxLength: 255, value: c.title || "" }),
    description: h("textarea", { id: "f-description", maxLength: 1000 }, c.description || ""),
    image_url: h("input", { id: "f-image", type: "url", placeholder: "https://…", value: c.image_url || "" }),
    target_url: h("input", { id: "f-target", type: "url", required: true, placeholder: "https://…", value: c.target_url || "" }),
    start_date: h("input", { id: "f-start", type: "datetime-local", value: toLocalInput(c.start_date) }),
    end_date: h("input", { id: "f-end", type: "datetime-local", value: toLocalInput(c.end_date) }),
  };
  const values = () => ({
    placement_id: Number(f.placement_id.value),
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
    preview.replaceChildren(adPreview({ title: v.title || "Заголовок объявления", description: v.description, image_url: v.image_url }));
  };
  [f.title, f.description, f.image_url].forEach((el) => el.addEventListener("input", updatePreview));
  updatePreview();

  const approved = original && ["active", "paused"].includes(original.status);
  const submit = h("button", { class: "primary", type: "submit" }, id ? "Сохранить" : "Создать черновик");
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
        if (approved && content && !confirm("Изменение текста, ссылки, картинки или площадки отправит кампанию на повторную модерацию — до одобрения она не будет показываться. Продолжить?")) return;
      }
      const saved = await run(submit, () => api(original ? "PATCH" : "POST", original ? `/campaigns/${id}` : "/campaigns", body),
        original ? "Сохранено" : "Черновик создан — отправьте его на модерацию");
      if (saved) location.hash = `#/campaigns/${saved.id}`;
    },
  },
  approved ? h("div", { class: "notice info" },
    "Кампания одобрена. Изменение содержимого отправит её на повторную модерацию; изменение дат — нет.") : null,
  h("div", { class: "form-grid" },
    h("div", { class: "field full" }, h("label", { for: "f-placement" }, "Площадка"), f.placement_id),
    h("div", { class: "field full" }, h("label", { for: "f-title" }, "Заголовок"), f.title),
    h("div", { class: "field full" }, h("label", { for: "f-description" }, "Описание"), f.description,
      h("div", { class: "hint" }, "До 1000 символов, необязательно")),
    h("div", { class: "field" }, h("label", { for: "f-image" }, "Картинка (URL)"), f.image_url,
      h("div", { class: "hint" }, "Необязательно. Только http(s)")),
    h("div", { class: "field" }, h("label", { for: "f-target" }, "Ссылка на сайт"), f.target_url),
    h("div", { class: "field" }, h("label", { for: "f-start" }, "Начало показа"), f.start_date,
      h("div", { class: "hint" }, "Пусто — сразу после одобрения")),
    h("div", { class: "field" }, h("label", { for: "f-end" }, "Окончание показа"), f.end_date,
      h("div", { class: "hint" }, "Пусто — бессрочно"))),
  h("div", { class: "field" }, h("label", {}, "Предпросмотр"), preview),
  h("div", { class: "row" }, submit, h("a", { class: "btn", href: id ? `#/campaigns/${id}` : "#/campaigns" }, "Отмена")));

  return h("div", {}, h("h1", {}, id ? "Изменить кампанию" : "Новая кампания"), form);
}

// ---------- Кошелёк ----------
async function walletView() {
  await refreshMe();
  const rows = h("tbody");
  const txRow = (t) => h("tr", {},
      h("td", { class: "nowrap" }, dateTime(t.created_at)),
      h("td", {}, TX_TYPE[t.type] || t.type),
      h("td", {}, t.description || "",
        t.campaign_id ? h("span", {}, " · ", h("a", { href: `#/campaigns/${t.campaign_id}` }, `кампания #${t.campaign_id}`)) : null),
      h("td", { class: "num", style: t.type === "click_spend" ? "" : "color:var(--ok)" },
        (t.type === "click_spend" ? "−" : "+") + money(t.amount)));
  const { more, first: count } = await cursorFeed("/wallet/history", 50, txRow, rows);

  const isAdmin = state.me.role === "admin";
  const amount = h("input", { type: "number", min: "0.01", max: "1000000", step: "0.01", placeholder: "Сумма", style: "width:140px" });
  const depositBtn = h("button", { class: "primary", type: "submit" }, "Пополнить");
  const depositForm = isAdmin ? h("form", {
    class: "row",
    onsubmit: async (e) => {
      e.preventDefault();
      const ok = await run(depositBtn, () => api("POST", "/wallet/deposit", { amount: amount.value }), "Баланс пополнен");
      if (ok) document.getElementById("app") && render();
    },
  }, amount, depositBtn) : h("p", { class: "muted small" },
    "Пополнение баланса выполняет администратор платформы.");

  return h("div", {},
    h("h1", {}, "Кошелёк"),
    h("div", { class: "grid" }, statCard("Баланс", money(state.me.balance))),
    h("div", { class: "card" }, h("h2", {}, "Пополнение"), depositForm),
    h("div", { class: "card" }, h("h2", {}, "История операций"),
      count ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Когда"), h("th", {}, "Тип"), h("th", {}, "Описание"), h("th", { class: "num" }, "Сумма"))),
        rows)) : h("div", { class: "empty" }, "Операций пока не было"),
      h("div", { style: "margin-top:10px" }, more)));
}

// ---------- Админ: модерация ----------
async function moderationView() {
  const page = await api("GET", "/campaigns?status=moderation&limit=200", undefined, { page: true });
  const queue = page.items;
  const wrap = h("div");
  const reload = async () => wrap.replaceWith(await moderationView());
  add(wrap, h("h1", {}, "Модерация ",
    queue.length ? h("span", { class: "badge count" }, page.hasMore ? "200+" : queue.length) : null),
  page.hasMore ? h("div", { class: "notice info" },
    "Показаны первые 200 заявок — после проверки обновите страницу, подгрузятся следующие.") : null);
  if (!queue.length) {
    add(wrap, h("div", { class: "card empty" }, "Очередь пуста — все кампании проверены"));
    return wrap;
  }
  queue.forEach((c) => {
    const reason = h("input", { placeholder: "Причина отказа (обязательно)", maxLength: 1000 });
    const approve = h("button", { class: "primary small" }, "Одобрить");
    const reject = h("button", { class: "danger small" }, "Отклонить");
    approve.onclick = async () => {
      if (await run(approve, () => api("PATCH", `/campaigns/${c.id}/moderate`, { status: "active" }), "Одобрено")) reload();
    };
    reject.onclick = async () => {
      if (!reason.value.trim()) { reason.focus(); toast("Укажите причину отказа", "error"); return; }
      const body = { status: "rejected", rejection_reason: reason.value.trim() };
      if (await run(reject, () => api("PATCH", `/campaigns/${c.id}/moderate`, body), "Отклонено")) reload();
    };
    add(wrap, h("div", { class: "card stack" },
      h("div", { class: "row between" },
        h("div", {}, h("b", {}, `#${c.id} `), c.title),
        h("span", { class: "small muted" }, c.owner_email, " · ", c.placement_name, " · ", dateTime(c.created_at))),
      adPreview(c),
      h("div", { class: "small" }, "Ссылка: ",
        h("a", { href: c.target_url, target: "_blank", rel: "noopener noreferrer", class: "break" }, c.target_url)),
      c.image_url ? h("div", { class: "small muted break" }, "Картинка: ", c.image_url) : null,
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
    name: h("input", { required: true, maxLength: 255, placeholder: "Баннер в шапке" }),
    code: h("input", { required: true, maxLength: 100, pattern: "[A-Za-z0-9_-]+", placeholder: "header_banner" }),
    ppc: h("input", { type: "number", min: "0", step: "0.01", value: "0" }),
    ppd: h("input", { type: "number", min: "0", step: "0.01", value: "0" }),
  };
  const createBtn = h("button", { class: "primary", type: "submit" }, "Создать");
  const createForm = h("form", {
    class: "card",
    onsubmit: async (e) => {
      e.preventDefault();
      const body = { name: nf.name.value.trim(), code_identifier: nf.code.value.trim(),
        price_per_click: nf.ppc.value || "0", price_per_day: nf.ppd.value || "0" };
      if (await run(createBtn, () => api("POST", "/placements", body), "Площадка создана")) reload();
    },
  },
  h("h2", {}, "Новая площадка"),
  h("div", { class: "form-grid" },
    h("div", { class: "field" }, h("label", {}, "Название"), nf.name),
    h("div", { class: "field" }, h("label", {}, "Код"), nf.code,
      h("div", { class: "hint" }, "Латиница, цифры, _ и -. Вставляется на сайт, потом не меняется")),
    h("div", { class: "field" }, h("label", {}, "Цена клика"), nf.ppc),
    h("div", { class: "field" }, h("label", {}, "Цена дня"), nf.ppd,
      h("div", { class: "hint" }, "Справочно: оплата за день пока не списывается"))),
  createBtn);

  const rows = list.map((p) => {
    const price = h("input", { type: "number", min: "0", step: "0.01", value: p.price_per_click });
    const save = h("button", { class: "small" }, "Сохранить");
    save.onclick = async () => {
      if (await run(save, () => api("PATCH", `/placements/${p.id}`, { price_per_click: price.value }), "Цена обновлена")) reload();
    };
    const toggle = h("button", { class: "small" + (p.is_active ? " danger" : "") }, p.is_active ? "Отключить" : "Включить");
    toggle.onclick = async () => {
      if (p.is_active && !confirm(`Отключить «${p.name}»? Реклама на ней перестанет показываться.`)) return;
      if (await run(toggle, () => api("PATCH", `/placements/${p.id}`, { is_active: !p.is_active }))) reload();
    };
    const snippet = embedCode(p.code_identifier);
    return h("tr", {},
      h("td", {}, h("b", {}, p.name), h("div", { class: "mono muted" }, p.code_identifier),
        p.is_active ? null : h("span", { class: "badge" }, "Отключена")),
      h("td", {}, h("div", { class: "inline-form" }, price, save)),
      h("td", {},
        h("pre", { class: "code mono" }, snippet),
        h("div", { class: "row" },
          h("button", { class: "small", onclick: () => copy(snippet) }, "Скопировать код"),
          h("a", { class: "btn small", href: `/demo?placement=${encodeURIComponent(p.code_identifier)}`, target: "_blank" }, "Демо"))),
      h("td", {}, toggle));
  });

  add(wrap, h("h1", {}, "Площадки"), createForm,
    h("div", { class: "card" }, table(["Площадка", "Цена клика", "Код для сайта", ""], rows, "Площадок пока нет")));
  return wrap;
}

// ---------- Админ: пользователи ----------
async function usersView(query = "") {
  const page = await api("GET", `/users?limit=200${query ? "&q=" + encodeURIComponent(query) : ""}`, undefined, { page: true });
  const list = page.items;
  const wrap = h("div");
  const reload = async () => wrap.replaceWith(await usersView(search.value.trim()));
  const search = h("input", { type: "search", placeholder: "Поиск по email", value: query, style: "max-width:320px" });
  const searchForm = h("form", { class: "row", onsubmit: (e) => { e.preventDefault(); reload(); } },
    search, h("button", { type: "submit" }, "Найти"));

  const rows = list.map((u) => {
    const amount = h("input", { type: "number", min: "0.01", step: "0.01", placeholder: "Сумма" });
    const deposit = h("button", { class: "small primary" }, "Пополнить");
    deposit.onclick = async () => {
      if (!amount.value) { amount.focus(); return; }
      const res = await run(deposit, () => api("POST", `/wallet/deposit?user_id=${u.id}`, { amount: amount.value }),
        `Баланс ${u.email} пополнен`);
      if (res) { if (u.id === state.me.id) await refreshMe(); reload(); }
    };
    const self = u.id === state.me.id;
    const roleBtn = h("button", { class: "small", disabled: self, title: self ? "Свою роль изменить нельзя" : "" },
      u.role === "admin" ? "Сделать рекламодателем" : "Сделать админом");
    roleBtn.onclick = async () => {
      const role = u.role === "admin" ? "advertiser" : "admin";
      if (!confirm(`${u.email}: сменить роль на «${role === "admin" ? "админ" : "рекламодатель"}»?`)) return;
      if (await run(roleBtn, () => api("PATCH", `/users/${u.id}/role`, { role }), "Роль изменена")) reload();
    };
    return h("tr", {},
      h("td", {}, u.email, h("div", { class: "small muted" }, `#${u.id} · с ${dateTime(u.created_at)}`)),
      h("td", {}, u.role === "admin" ? h("span", { class: "badge paused" }, "админ") : "рекламодатель"),
      h("td", { class: "num" }, money(u.balance)),
      h("td", {}, h("div", { class: "inline-form" }, amount, deposit)),
      h("td", {}, roleBtn));
  });
  add(wrap, h("h1", {}, "Пользователи"),
    h("div", { class: "card" }, searchForm,
      page.hasMore ? h("p", { class: "small muted", style: "margin:8px 0 0" },
        "Показаны 200 последних — уточните поиск по email, чтобы найти остальных.") : null),
    h("div", { class: "card" }, table(["Пользователь", "Роль", { label: "Баланс", num: 1 }, "Пополнение", ""], rows,
      query ? "Никого не найдено" : "Пользователей нет")));
  return wrap;
}

// ---------- Админ: платформа ----------
async function platformView(days = 30) {
  const s = await api("GET", `/stats/platform?days=${days}`);
  const wrap = h("div");
  const reload = async (n) => wrap.replaceWith(await platformView(n));
  add(wrap,
    h("div", { class: "row between" }, h("h1", {}, "Платформа"), daysSelect(days, reload)),
    h("div", { class: "grid" },
      statCard("Выручка за период", money(s.totals.spend)),
      statCard("Показы", int(s.totals.impressions)),
      statCard("Клики", int(s.totals.clicks)),
      statCard("CTR", ctr(s.totals)),
      statCard("Пользователей", int(s.users_count)),
      statCard("Активных кампаний", int(s.active_campaigns)),
      statCard("На модерации", int(s.moderation_queue)),
      statCard("Деньги на балансах", money(s.advertisers_balance))),
    h("div", { class: "card" }, h("h2", {}, "По дням"), chart(s.days)),
    h("div", { class: "card" }, h("h2", {}, "По площадкам"),
      table(["Площадка", { label: "Показы", num: 1 }, { label: "Клики", num: 1 }, { label: "CTR", num: 1 }, { label: "Выручка", num: 1 }],
        s.placements.map((p) => h("tr", {},
          h("td", {}, p.name, " ", h("span", { class: "mono muted" }, p.code_identifier),
            p.is_active ? null : h("span", { class: "badge" }, " отключена")),
          h("td", { class: "num" }, int(p.impressions)), h("td", { class: "num" }, int(p.clicks)),
          h("td", { class: "num" }, ctr(p)), h("td", { class: "num" }, money(p.spend)))),
        "Площадок нет")));
  return wrap;
}

// ---------- Профиль: смена пароля ----------
async function profileView() {
  const cur = h("input", { type: "password", id: "p-current", required: true, autocomplete: "current-password" });
  const next = h("input", { type: "password", id: "p-new", required: true, minLength: 8, autocomplete: "new-password" });
  const again = h("input", { type: "password", id: "p-again", required: true, minLength: 8, autocomplete: "new-password" });
  const submit = h("button", { class: "primary", type: "submit" }, "Сменить пароль");
  const form = h("form", {
    class: "card",
    style: "max-width:420px",
    onsubmit: async (e) => {
      e.preventDefault();
      if (next.value !== again.value) { toast("Новые пароли не совпадают", "error"); again.focus(); return; }
      const res = await run(submit, () => api("POST", "/auth/change-password",
        { current_password: cur.value, new_password: next.value }),
      "Пароль изменён. На других устройствах нужно войти заново");
      if (res) { writeToken(res.access_token); form.reset(); }
    },
  },
  h("h2", {}, "Смена пароля"),
  h("div", { class: "field" }, h("label", { for: "p-current" }, "Текущий пароль"), cur),
  h("div", { class: "field" }, h("label", { for: "p-new" }, "Новый пароль"), next,
    h("div", { class: "hint" }, "Не короче 8 символов")),
  h("div", { class: "field" }, h("label", { for: "p-again" }, "Новый пароль ещё раз"), again),
  submit);
  return h("div", {},
    h("h1", {}, "Профиль"),
    h("div", { class: "card small" }, h("div", {}, "Email: ", h("b", {}, state.me.email)),
      h("div", {}, "Роль: ", state.me.role === "admin" ? "администратор" : "рекламодатель"),
      h("div", { class: "muted" }, "Зарегистрирован: ", dateTime(state.me.created_at))),
    form);
}

// ---------- Старт ----------
window.addEventListener("hashchange", render);
render();
