"""Клиент API платформы для бота: /api/v1/bot/* с секретом в заголовке X-Bot-Secret.

Бот действует от имени пользователя по его telegram_id — бэкенд проверяет, что этот Telegram
привязан к аккаунту, и применяет те же правила, что и сайт (модерация, лимиты, оплата).
Сообщения об ошибках сервер присылает на языке пользователя (заголовок Accept-Language).
"""
import copy
from dataclasses import dataclass

import aiohttp

from texts import DEFAULT_LANGUAGE, Texts


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class Account:
    email: str
    balance: float
    held_balance: float


def _detail(data: object, status: int, t: Texts) -> str:
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):  # 422: список ошибок полей
        return "; ".join(str(e.get("msg", e)) for e in detail if isinstance(e, dict)) or t("api_error", status=status)
    return t("api_server_error", status=status)


class PlatformAPI:
    def __init__(self, base_url: str, bot_secret: str, timeout_seconds: float = 30):
        self._base = f"{base_url}/api/v1/bot"
        self._headers = {"X-Bot-Secret": bot_secret}
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        # Одна сессия на всех: копии with_language() пользуются ею же
        self._shared: dict[str, aiohttp.ClientSession | None] = {"session": None}
        self._lang = DEFAULT_LANGUAGE

    def with_language(self, lang: str) -> "PlatformAPI":
        """Тот же клиент, но ответы сервера — на языке lang (на каждое сообщение пользователя — своя копия)."""
        view = copy.copy(self)
        view._lang = lang
        return view

    async def _request(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None):
        session = self._shared["session"]
        if session is None or session.closed:
            session = self._shared["session"] = aiohttp.ClientSession(headers=self._headers, timeout=self._timeout)
        t = Texts(self._lang)
        try:
            async with session.request(method, self._base + path, json=json, params=params,
                                       headers={"Accept-Language": self._lang}) as r:
                data = await r.json(content_type=None) if r.status != 204 else None
                if r.status >= 400:
                    raise ApiError(r.status, _detail(data, r.status, t))
                return data
        except aiohttp.ClientError as e:
            raise ApiError(0, t("api_unavailable")) from e
        except TimeoutError as e:
            raise ApiError(0, t("api_timeout")) from e

    async def close(self) -> None:
        if self._shared["session"] is not None:
            await self._shared["session"].close()

    async def link(self, telegram_id: int, code: str) -> Account:
        return Account(**await self._request("POST", "/link", json={"code": code, "telegram_id": telegram_id}))

    async def unlink(self, telegram_id: int) -> None:
        await self._request("POST", "/unlink", json={"telegram_id": telegram_id})

    async def me(self, telegram_id: int) -> Account:
        return Account(**await self._request("GET", "/me", params={"telegram_id": telegram_id}))

    async def generate(self, telegram_id: int, product_description: str, target_audience: str,
                       language: str) -> dict:
        """language — язык объявлений (en/ru/de)."""
        return await self._request("POST", "/generate", json={
            "telegram_id": telegram_id, "product_description": product_description,
            "target_audience": target_audience, "language": language})

    async def task(self, telegram_id: int, task_id: str) -> dict:
        return await self._request("GET", f"/tasks/{task_id}", params={"telegram_id": telegram_id})
