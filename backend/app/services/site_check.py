"""Автоматическая проверка сайта партнёра: открывается ли он и что на нём (оценка ИИ).

1. fetch_page — загружает главную страницу. Адрес вводит пользователь, поэтому это запрос «от имени
   сервера» к чужому адресу (SSRF): разрешены только http/https, порты 80/443 и только публичные
   IP-адреса — каждый переход по редиректу проверяется заново. Иначе через «сайт» можно было бы
   прочитать внутренние адреса (169.254.169.254 — секреты облачного сервера, 127.0.0.1, 10.x…).
   Итоговый адрес после редиректов должен остаться на домене сайта.
2. ai.moderate_site — оценка содержимого (approve / review / reject и риск).
3. Решение: уверенное «одобрить» (+ низкий риск) — сайт одобряется сам (AI_AUTO_APPROVE),
   уверенное «отклонить» (+ высокий риск) — отклоняется (AI_AUTO_REJECT). Остальное — администратору.
   Сайт не открылся — остаётся на проверке; ежедневная задача проверяет такие сайты снова.
"""
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai
from app.config import settings
from app.database import write_lock
from app.models import Site, SiteStatus
from app.services import notify

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10
MAX_BYTES = 512 * 1024      # главной странице хватает; больше не читаем
MAX_REDIRECTS = 5
TEXT_LIMIT = 4000           # символов текста странице для модели — дёшево и достаточно для вывода
USER_AGENT = "AdPlatformSiteCheck/1.0 (+site review for the ad network)"
VERIFY_META = "adplatform-site-verification"
AUTO_PREFIX = "Автоматическая проверка: "


class FetchError(Exception):
    """Страницу загрузить не удалось (текст — для администратора)."""


@dataclass
class Page:
    url: str
    title: str
    text: str
    verification: str | None = None  # content тега <meta name="adplatform-site-verification">


def _public_ip(host: str) -> str | None:
    """IP-адрес хоста, если ВСЕ его адреса публичные (не локальные, не частные, не служебные); иначе None."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return None
    addresses = sorted({info[4][0].split("%")[0] for info in infos})
    if not addresses or not all(ipaddress.ip_address(a).is_global for a in addresses):
        return None
    return next((a for a in addresses if ":" not in a), addresses[0])  # IPv4, если есть


def _check_url(url: str) -> str:
    """Проверяет адрес и возвращает проверенный IP, к которому и подключаться."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("адрес не http/https")
    if parts.port not in (None, 80, 443):
        raise FetchError("нестандартный порт")
    ip = _public_ip(parts.hostname)
    if ip is None:
        raise FetchError("адрес не найден или не публичный")
    return ip


def _pinned(url: str, ip: str) -> tuple[str, dict, dict]:
    """Запрос к уже проверенному IP (а не к имени, которое DNS при подключении мог бы разрешить
    иначе — «DNS rebinding»: сначала публичный адрес, потом 169.254.169.254). Имя сайта — в Host
    и в TLS (SNI): сервер отдаёт нужный сайт, сертификат проверяется по имени."""
    parts = urlsplit(url)
    host = parts.hostname
    netloc = f"[{ip}]" if ":" in ip else ip
    if parts.port:
        netloc += f":{parts.port}"
    target = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
    return target, {"Host": parts.netloc.rsplit("@", 1)[-1]}, extensions


class _TextExtractor(HTMLParser):
    """Заголовок и видимый текст страницы (без скриптов и стилей)."""

    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.parts, self._skip, self._in_title = "", [], 0, False
        self.verification: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            a = dict(attrs)
            if (a.get("name") or "").lower() == VERIFY_META and self.verification is None:
                self.verification = (a.get("content") or "").strip()
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def extract_text(html: str) -> tuple[str, str]:
    title, text, _ = _extract(html)
    return title, text


def _extract(html: str) -> tuple[str, str, str | None]:
    parser = _TextExtractor()
    parser.feed(html)
    text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    return re.sub(r"\s+", " ", parser.title).strip()[:300], text[:TEXT_LIMIT], parser.verification


def _on_domain(host: str, domain: str) -> bool:
    host = host.lower().rstrip(".")
    host = host[4:] if host.startswith("www.") else host
    return host == domain or host.endswith("." + domain)


def fetch_page(url: str, domain: str, transport: httpx.BaseTransport | None = None) -> Page:
    """Загружает страницу с проверкой каждого перехода. FetchError — если не удалось."""
    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False, transport=transport,
                      headers={"User-Agent": USER_AGENT, "Accept": "text/html"}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            target, headers, extensions = _pinned(url, _check_url(url))
            try:
                with client.stream("GET", target, headers=headers, extensions=extensions) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    if response.status_code != 200:
                        raise FetchError(f"сайт ответил кодом {response.status_code}")
                    if "html" not in response.headers.get("content-type", "html"):
                        raise FetchError("главная страница — не HTML")
                    body = b""
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) >= MAX_BYTES:
                            break
                    body = body[:MAX_BYTES]  # кусок мог быть больше лимита целиком
                    encoding = response.encoding or "utf-8"
            except httpx.HTTPError as e:
                raise FetchError(f"сайт не открывается ({type(e).__name__})") from e
            if not _on_domain(urlsplit(url).hostname or "", domain):
                raise FetchError("сайт перенаправляет на другой домен")
            title, text, verification = _extract(body.decode(encoding, errors="replace"))
            return Page(url=url, title=title, text=text, verification=verification)
    raise FetchError("слишком много перенаправлений")


def check_site(session_factory: Callable[[], Session], site_id: int,
               transport: httpx.BaseTransport | None = None) -> Site | None:
    """Проверяет сайт и сохраняет итог; решает сам, только если сайт всё ещё на проверке.
    Сеть и модель — вне транзакции БД. Возвращает сайт (отсоединённый от сессии) или None."""
    with session_factory() as db:
        site = db.get(Site, site_id)
        if site is None:
            return None
        url, domain, token, verified = site.url, site.domain, site.verify_token, site.verified_at is not None

    verdict, summary, result = None, None, None
    try:
        page = fetch_page(url, domain, transport)
    except FetchError as e:
        verdict, summary = "unreachable", f"Сайт не открылся: {e}"
    else:
        just_verified = bool(token) and page.verification == token
        if not (verified or just_verified):
            # Без кода владельца сайт не одобряется и даже не оценивается: иначе любой мог бы добавить
            # чужой сайт (ИИ увидел бы обычный сайт настоящего владельца) и получать доход с поддоменов
            verdict, summary = "unverified", "Код подтверждения не найден на главной странице сайта"
        elif not ai.is_enabled():
            verdict, summary = "reachable", page.title or url
        else:
            try:
                result = ai.moderate_site(domain, page.title, page.text)
                verdict, summary = result.verdict, result.summary
            except ai.AIUnavailable as e:
                verdict, summary = "error", str(e)

    with write_lock(), session_factory() as db:
        site = db.get(Site, site_id)
        if site is None:
            return None
        if verdict not in ("unreachable", "unverified") and site.verified_at is None:
            site.verified_at = datetime.now(timezone.utc)  # код найден: владение подтверждено один раз
        site.check_verdict, site.check_summary = verdict, (summary or "")[:1000]
        site.check_reasons = result.reasons if result else None
        site.checked_at = datetime.now(timezone.utc)
        before = site.status
        # Решение админа и приостановку за накрутку не трогаем: снимает её только администратор
        if site.status == SiteStatus.PENDING and not site.fraud_hold and result is not None:
            if settings.ai_auto_reject and result.verdict == "reject" and result.risk == "high":
                site.status = SiteStatus.REJECTED
                site.rejection_reason = (AUTO_PREFIX + ("; ".join(result.reasons) or result.summary))[:1000]
            elif settings.ai_auto_approve and result.verdict == "approve" and result.risk == "low":
                site.status, site.rejection_reason = SiteStatus.APPROVED, None
        db.commit()
        db.refresh(site)
        if site.status != before:
            notify.site_decided(site, site.owner)
        db.expunge(site)
        return site


def retry_errors(session_factory: Callable[[], Session]) -> int:
    """Часто (каждые 5 минут): сайты на проверке, по которым модель не ответила, — первые сутки,
    не чаще раза в 5 минут. Не открывшиеся сайты — реже, в recheck_pending раз в сутки."""
    if not (settings.site_auto_check and ai.is_enabled()):
        return 0
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        ids = db.scalars(select(Site.id).where(
            Site.status == SiteStatus.PENDING, Site.check_verdict == "error",
            Site.checked_at < now - timedelta(minutes=5), Site.created_at >= now - timedelta(hours=24),
        ).order_by(Site.id).limit(10)).all()
    for site_id in ids:
        try:
            check_site(session_factory, site_id)
        except Exception:  # один сбойный сайт не останавливает остальные
            log.exception("Ошибка повторной проверки сайта #%s", site_id)
    return len(ids)


def recheck_pending(session_factory: Callable[[], Session]) -> int:
    """Ежедневно: сайты на проверке, которые ещё не проверялись или не открылись / модель не ответила."""
    with session_factory() as db:
        ids = db.scalars(select(Site.id).where(
            Site.status == SiteStatus.PENDING,
            (Site.check_verdict.is_(None))
            | (Site.check_verdict.in_(("unreachable", "unverified", "error", "reachable"))),
        ).limit(200)).all()
    for site_id in ids:
        try:
            check_site(session_factory, site_id)
        except Exception:  # один сбойный сайт не останавливает проверку остальных
            log.exception("Ошибка проверки сайта #%s", site_id)
    return len(ids)
