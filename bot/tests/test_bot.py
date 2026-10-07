"""Бот целиком через диспетчер aiogram: Telegram и API платформы подменены.

Сообщения проходят настоящую маршрутизацию aiogram (фильтры команд, состояние диалога),
а «отправленное ботом» записывает подменённая сессия.
"""
import asyncio
import datetime
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.client.default import Default
from aiogram.client.session.base import BaseSession
from aiogram.methods import EditMessageText, SendMessage, TelegramMethod
from aiogram.types import Chat, Message, Update, User

import main
from api_client import Account, ApiError
from handlers import generate

USER_ID = 555
SITE = "https://ads.example.com"


class RecordingSession(BaseSession):
    """Вместо запросов к Telegram — запись: что бот отправил и что отредактировал."""

    def __init__(self):
        super().__init__()
        self.sent: list[str] = []
        self.edited: list[str] = []
        self._next_id = 1000

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None):
        if isinstance(method, SendMessage):
            self.sent.append(method.text)
            # Итоговый parse_mode (с учётом настроек бота) — None: обычный текст, разметка не сработает
            mode = bot.default[method.parse_mode.name] if isinstance(method.parse_mode, Default) else method.parse_mode
            assert mode is None
            self._next_id += 1
            # .as_(bot) — как делает настоящая сессия: у ответа можно вызвать edit_text
            return Message(message_id=self._next_id, date=datetime.datetime.now(),
                           chat=Chat(id=method.chat_id, type="private"), text=method.text).as_(bot)
        if isinstance(method, EditMessageText):
            self.edited.append(method.text)
            return True
        raise AssertionError(f"неожиданный вызов Telegram: {type(method).__name__}")

    async def stream_content(self, *args, **kwargs):  # не используется
        raise NotImplementedError
        yield b""

    async def close(self):
        pass


class FakeAPI:
    """Подменяет PlatformAPI: ответы и ошибки задаются в тесте, вызовы записываются."""

    def __init__(self):
        self.calls = []
        self.languages = []  # язык каждого обращения (Accept-Language у настоящего клиента)
        self.linked = True
        self.account = Account(email="adv@example.com", balance=12.5, held_balance=0.0)
        self.generate_error: ApiError | None = None
        self.task_states = [{"status": "processing"},
                            {"status": "completed", "result": {"variants": [
                                {"title": "Python с нуля", "text": "40 уроков", "cta": "Записаться"}]}}]

    def with_language(self, lang):
        self.languages.append(lang)
        return self

    def _check_linked(self):
        if not self.linked:
            raise ApiError(404, "Telegram не привязан к аккаунту")

    async def link(self, telegram_id, code):
        self.calls.append(("link", telegram_id, code))
        if code != "GOODCODE":
            raise ApiError(400, "Код неверный или устарел — получите новый в кабинете")
        self.linked = True
        return self.account

    async def unlink(self, telegram_id):
        self.calls.append(("unlink", telegram_id))
        self._check_linked()
        self.linked = False

    async def me(self, telegram_id):
        self.calls.append(("me", telegram_id))
        self._check_linked()
        return self.account

    async def generate(self, telegram_id, product_description, target_audience, language):
        self.calls.append(("generate", telegram_id, product_description, target_audience, language))
        self._check_linked()
        if self.generate_error:
            raise self.generate_error
        return {"task_id": "t1", "status": "pending", "held_amount": 0.03}

    async def task(self, telegram_id, task_id):
        self.calls.append(("task", telegram_id, task_id))
        return self.task_states.pop(0) if len(self.task_states) > 1 else self.task_states[0]


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    # Опрос статуса без реальных пауз
    real_wait = generate.wait_for_task

    async def no_sleep(_seconds):
        await asyncio.sleep(0)
    def fast_wait(api, tg, task_id, **kw):
        kw.setdefault("sleep", no_sleep)
        return real_wait(api, tg, task_id, **kw)
    monkeypatch.setattr(generate, "wait_for_task", fast_wait)


@pytest.fixture
def chat():
    """Диалог с ботом: chat.send("/команда") → что бот ответил."""
    session = RecordingSession()
    bot = Bot("42:TEST-TOKEN", session=session)
    api = FakeAPI()
    dp = main.build_dispatcher(api, SITE)
    counter = {"n": 0}

    def send(text: str, lang: str | None = "ru") -> list[str]:
        """lang — язык Telegram пользователя (language_code); по умолчанию русский."""
        counter["n"] += 1
        before = len(session.sent)
        update = Update(update_id=counter["n"], message=Message(
            message_id=counter["n"], date=datetime.datetime.now(), text=text,
            chat=Chat(id=USER_ID, type="private"),
            from_user=User(id=USER_ID, is_bot=False, first_name="Тест", language_code=lang)))
        asyncio.run(dp.feed_update(bot, update))
        return session.sent[before:]

    return SimpleNamespace(send=send, api=api, session=session)


def test_start_linked(chat):
    [reply] = chat.send("/start")
    assert "adv@example.com" in reply and "/generate" in reply


def test_start_not_linked_explains_how(chat):
    chat.api.linked = False
    [reply] = chat.send("/start")
    assert "не привязан" in reply and f"{SITE}/app" in reply and "/link КОД" in reply


def test_deep_link_binds_account(chat):
    chat.api.linked = False
    [reply] = chat.send("/start GOODCODE")
    assert ("link", USER_ID, "GOODCODE") in chat.api.calls
    assert "adv@example.com привязан" in reply and "12,50" in reply


def test_link_command_and_bad_code(chat):
    [reply] = chat.send("/link WRONG123")
    assert "Код неверный" in reply and "/link КОД" in reply
    [reply] = chat.send("/link")
    assert "/link КОД" in reply


def test_balance(chat):
    chat.api.account = Account(email="adv@example.com", balance=1234.5, held_balance=0.03)
    [reply] = chat.send("/balance")
    assert "1 234,50" in reply and "Заморожено под генерации: 0,03" in reply
    chat.api.linked = False
    [reply] = chat.send("/balance")
    assert "не привязан" in reply


def test_unlink(chat):
    [reply] = chat.send("/unlink")
    assert "отвязан" in reply and chat.api.linked is False
    [reply] = chat.send("/unlink")
    assert "и так не привязан" in reply


def test_generate_dialog(chat):
    [ask] = chat.send("/generate")
    assert "Опишите товар" in ask
    [progress] = chat.send("Онлайн-курс Python для начинающих\nАудитория: студенты")
    assert "заморожено 0,03" in progress
    assert ("generate", USER_ID, "Онлайн-курс Python для начинающих", "студенты", "ru") in chat.api.calls
    [result] = chat.session.edited
    assert "Заголовок: Python с нуля" in result and "Призыв: Записаться" in result
    # Диалог завершён: следующий текст — не описание товара
    [reply] = chat.send("просто текст")
    assert "Не понял команду" in reply


def test_generate_one_shot_and_default_audience(chat):
    chat.send("/generate Кофейня у метро, завтраки до 12:00")
    assert ("generate", USER_ID, "Кофейня у метро, завтраки до 12:00", "Общая аудитория", "ru") in chat.api.calls


def test_generate_too_short_and_cancel(chat):
    chat.send("/generate")
    [reply] = chat.send("кофе")
    assert "от 10 символов" in reply
    assert not any(c[0] == "generate" for c in chat.api.calls)
    chat.send("/generate")
    [reply] = chat.send("/cancel")
    assert "Отменено" in reply
    [reply] = chat.send("Онлайн-курс Python для начинающих")  # после отмены — не описание
    assert "Не понял команду" in reply


@pytest.mark.parametrize("status,detail", [
    (402, "Недостаточно средств для AI-генерации"),
    (422, "Текст не прошёл модерацию: азартные игры"),
    (429, "Уже выполняется 5 AI-задач"),
    (503, "AI-копирайтер выключен"),
])
def test_generate_errors_show_server_reason(chat, status, detail):
    chat.api.generate_error = ApiError(status, detail)
    [reply] = chat.send("/generate Онлайн-курс Python для начинающих")
    assert reply == detail


def test_generate_failed_task(chat):
    chat.api.task_states = [{"status": "failed", "error": "превышен лимит. Деньги не списаны"}]
    chat.send("/generate Онлайн-курс Python для начинающих")
    assert chat.session.edited == ["превышен лимит. Деньги не списаны"]


def test_model_text_sent_as_plain_text(chat):
    xss = "<b>жирный</b> <a href='http://evil'>ссылка</a>"
    chat.api.task_states = [{"status": "completed", "result": {"variants": [{"title": xss, "text": "t", "cta": "c"}]}}]
    chat.send("/generate Онлайн-курс Python для начинающих")
    assert xss in chat.session.edited[0]  # как есть, без parse_mode (проверяет RecordingSession)


def test_wait_for_task_gives_up():
    class Slow:
        async def task(self, *_):
            return {"status": "processing"}

    async def no_sleep(_):
        await asyncio.sleep(0)
    assert asyncio.run(generate.wait_for_task(Slow(), 1, "t", sleep=no_sleep, limit_seconds=0.05)) is None


def test_parse_input():
    assert generate.parse_input("Курс Python\nАудитория: студенты\nещё строка", "Все") == (
        "Курс Python\nещё строка", "студенты")
    assert generate.parse_input("  Курс  ", "Все") == ("Курс", "Все")
    # Подпись аудитории — на любом из трёх языков
    assert generate.parse_input("Python course\nAudience: students", "-") == ("Python course", "students")
    assert generate.parse_input("Kurs\nZielgruppe: Studierende", "-") == ("Kurs", "Studierende")


# ---------- Языки ----------
def test_english_user(chat):
    [reply] = chat.send("/start", lang="en")
    assert reply.startswith("Hello! Account: adv@example.com") and "/generate" in reply
    chat.api.account = Account(email="adv@example.com", balance=1234.5, held_balance=0.03)
    [reply] = chat.send("/balance", lang="en")
    assert "Balance: 1,234.50" in reply and "On hold for generations: 0.03" in reply
    chat.send("/generate Python course for beginners", lang="en")
    assert ("generate", USER_ID, "Python course for beginners", "General audience", "en") in chat.api.calls
    assert "Headline: Python с нуля" in chat.session.edited[0]
    assert chat.api.languages[-1] == "en"  # ошибки сервера придут по-английски


def test_german_user(chat):
    chat.api.linked = False
    [reply] = chat.send("/start", lang="de")
    assert reply.startswith("Hallo!") and f"({SITE}/app)" in reply and "/link CODE" in reply
    chat.api.linked = True
    chat.api.account = Account(email="adv@example.com", balance=1234.5, held_balance=0.0)
    [reply] = chat.send("/balance", lang="de")
    assert "Guthaben: 1.234,50" in reply
    chat.send("/generate Kaffee am Bahnhof, Frühstück bis 12 Uhr", lang="de")
    assert ("generate", USER_ID, "Kaffee am Bahnhof, Frühstück bis 12 Uhr", "Allgemeines Publikum", "de") \
        in chat.api.calls


@pytest.mark.parametrize("code", ["fr", "pt-br", None])
def test_other_languages_get_english(chat, code):
    [reply] = chat.send("/help", lang=code)
    assert reply.startswith("What the bot can do")


def test_menu_commands_in_every_language():
    for lang in ("en", "ru", "de"):
        cmds = main.commands(lang)
        assert [c.command for c in cmds] == ["generate", "balance", "link", "unlink", "help"]
        assert all(c.description for c in cmds)
    assert main.commands("de")[0].description == "Anzeige schreiben"
