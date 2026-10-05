/*
 * Ad Platform — виджет показа рекламы на сайтах-партнёрах.
 *
 * Вариант 1 (баннер там, где стоит тег):
 *   <script async src="https://ВАШ-СЕРВЕР/widget.js" data-placement="header_top_banner"></script>
 *
 * Вариант 2 (несколько мест на странице, скрипт один):
 *   <div data-adp-placement="header_top_banner"></div>
 *   <div data-adp-placement="sidebar_banner"></div>
 *   <script async src="https://ВАШ-СЕРВЕР/widget.js"></script>
 *
 * Если для площадки нет рекламы, место остаётся пустым и не занимает высоту.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script || !script.src) return;
  var API = new URL(script.src, location.href).origin + "/api/v1/ad/serve";

  var STYLE = [
    ":host{all:initial;display:block;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}",
    "a{display:flex;gap:12px;align-items:center;padding:10px 12px;border:1px solid #d9dde3;",
    "border-radius:8px;background:#fff;color:#1a1d21;text-decoration:none;position:relative;",
    "box-sizing:border-box;max-width:100%;line-height:1.35}",
    "a:hover{border-color:#8a93a0}",
    "img{width:96px;height:72px;object-fit:cover;border-radius:6px;flex:none;background:#f1f3f5}",
    ".t{font-weight:600;font-size:15px;margin:0 0 2px}",
    ".d{font-size:13px;color:#4b5563;margin:0}",
    ".l{position:absolute;top:4px;right:8px;font-size:10px;color:#8a93a0;letter-spacing:.02em}",
    "@media (prefers-color-scheme:dark){a{background:#1f2328;color:#e6e8eb;border-color:#3a4048}",
    ".d{color:#a9b1bb}a:hover{border-color:#6b7480}}"
  ].join("");

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text; // textContent, не innerHTML: текст рекламодателя не исполняется
    return node;
  }

  function render(host, ad) {
    var root = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
    var style = document.createElement("style");
    style.textContent = STYLE;

    var link = el("a");
    link.href = ad.click_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer sponsored";

    if (ad.image_url) {
      var img = el("img");
      img.src = ad.image_url;
      img.alt = ad.title;
      img.loading = "lazy";
      img.onerror = function () { img.remove(); };
      link.appendChild(img);
    }
    var body = el("div");
    body.appendChild(el("p", "t", ad.title));
    if (ad.description) body.appendChild(el("p", "d", ad.description));
    link.appendChild(body);
    link.appendChild(el("span", "l", "Реклама"));

    root.appendChild(style);
    root.appendChild(link);
  }

  function load(host, placement) {
    if (!placement || host.getAttribute("data-adp-loaded")) return;
    host.setAttribute("data-adp-loaded", "1"); // защита от повторной вставки, если скрипт подключён дважды
    // empty_status=204: если рекламы нет, сервер ответит 204, а не 404 — без ошибки в консоли сайта
    fetch(API + "?empty_status=204&placement_code=" + encodeURIComponent(placement), { credentials: "omit" })
      .then(function (r) { return r.status === 200 ? r.json() : null; })
      .then(function (ad) { if (ad) render(host, ad); })
      .catch(function () { /* нет рекламы или сеть недоступна — место остаётся пустым */ });
  }

  function init() {
    var nodes = document.querySelectorAll("[data-adp-placement]");
    for (var i = 0; i < nodes.length; i++) load(nodes[i], nodes[i].getAttribute("data-adp-placement"));

    var own = script.getAttribute("data-placement");
    if (own) {
      var holder = document.createElement("div");
      if (script.parentNode && script.parentNode !== document.head) {
        script.parentNode.insertBefore(holder, script.nextSibling);
      } else {
        document.body.insertBefore(holder, document.body.firstChild); // тег в <head> — баннер в начало страницы
      }
      load(holder, own);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
