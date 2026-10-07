"""Тексты бота на трёх языках (обычный текст, без HTML/Markdown: текст объявлений приходит от модели).

Язык — из настроек Telegram пользователя (language_code): en, ru, de; другой — английский, как на сайте.
Новый язык: код в LANGUAGES и словарь с теми же ключами (полноту проверяет tests/test_texts.py).
"""

LANGUAGES = ("en", "ru", "de")
DEFAULT_LANGUAGE = "en"

# «Аудитория: …» во второй строке описания — на любом из языков
AUDIENCE_PREFIXES = ("аудитория:", "audience:", "zielgruppe:")

MESSAGES: dict[str, dict[str, str]] = {
    "ru": {
        "help": (
            "Что умеет бот:\n"
            "/generate — составить рекламное объявление (3 варианта)\n"
            "/balance — баланс аккаунта\n"
            "/link КОД — привязать аккаунт (код — в профиле кабинета)\n"
            "/unlink — отвязать аккаунт\n"
            "/cancel — отменить ввод"
        ),
        "ask_description": (
            "Опишите товар или услугу: что это, чем хороши, цена, особенности (от 10 символов).\n"
            "Аудиторию можно указать с новой строки после «Аудитория:».\n\n"
            "/cancel — отменить"
        ),
        "not_linked": (
            "Telegram ещё не привязан к аккаунту платформы.\n\n"
            "1. Откройте профиль в {cabinet} и нажмите «Получить код привязки».\n"
            "2. Нажмите «Открыть бота и привязать» или отправьте сюда: /link КОД"
        ),
        "cabinet": "кабинете платформы",
        "hello": "Здравствуйте! Аккаунт: {email}",
        "hello_unlinked": "Здравствуйте! Я помогаю рекламодателям платформы Ad Platform.",
        "link_usage": "Отправьте код вместе с командой: /link КОД",
        "link_failed": "Не удалось привязать: {detail}",
        "linked": "Аккаунт {email} привязан.\nБаланс: {balance}",
        "balance": "Аккаунт: {email}\nБаланс: {balance}",
        "held": "Заморожено под генерации: {amount}",
        "unlinked": "Аккаунт отвязан. Привязать снова — кодом из профиля в {cabinet}",
        "already_unlinked": "Telegram и так не привязан.",
        "too_short": "Опишите подробнее — от {count} символов.",
        "progress": "Составляю объявление… На время генерации заморожено {amount}, спишется по факту.",
        "result_error": "Не удалось получить результат: {detail}",
        "timeout": "Генерация идёт дольше обычного. Если задача не завершится, деньги вернутся автоматически.",
        "failed": "Генерация не удалась. Деньги не списаны",
        "variant": "Вариант {n}\nЗаголовок: {title}\nТекст: {text}\nПризыв: {cta}",
        "variants_header": "Готово:",
        "variants_footer": "Скопируйте понравившийся вариант в форму кампании в кабинете.",
        "default_audience": "Общая аудитория",
        "cancelled": "Отменено.",
        "unknown": "Не понял команду.",
        "api_unavailable": "Платформа сейчас недоступна — попробуйте позже",
        "api_timeout": "Платформа не ответила вовремя — попробуйте позже",
        "api_error": "Ошибка {status}",
        "api_server_error": "Сервер платформы ответил ошибкой ({status})",
        "cmd_generate": "Составить объявление",
        "cmd_balance": "Баланс",
        "cmd_link": "Привязать аккаунт кодом",
        "cmd_unlink": "Отвязать аккаунт",
        "cmd_help": "Что умеет бот",
    },
    "en": {
        "help": (
            "What the bot can do:\n"
            "/generate — write an ad (3 variants)\n"
            "/balance — account balance\n"
            "/link CODE — link your account (the code is in your dashboard profile)\n"
            "/unlink — unlink your account\n"
            "/cancel — cancel input"
        ),
        "ask_description": (
            "Describe your product or service: what it is, its strengths, price, features (at least 10 characters).\n"
            "You can add the audience on a new line after “Audience:”.\n\n"
            "/cancel — cancel"
        ),
        "not_linked": (
            "Telegram isn't linked to a platform account yet.\n\n"
            "1. Open your profile in {cabinet} and click “Get a link code”.\n"
            "2. Click “Open the bot and link” or send here: /link CODE"
        ),
        "cabinet": "the platform dashboard",
        "hello": "Hello! Account: {email}",
        "hello_unlinked": "Hello! I help advertisers on the Ad Platform.",
        "link_usage": "Send the code together with the command: /link CODE",
        "link_failed": "Couldn't link: {detail}",
        "linked": "Account {email} linked.\nBalance: {balance}",
        "balance": "Account: {email}\nBalance: {balance}",
        "held": "On hold for generations: {amount}",
        "unlinked": "Account unlinked. To link again, use the code from your profile in {cabinet}",
        "already_unlinked": "Telegram isn't linked anyway.",
        "too_short": "Please describe it in more detail — at least {count} characters.",
        "progress": "Writing your ad… {amount} is held during generation and charged at actual cost.",
        "result_error": "Couldn't get the result: {detail}",
        "timeout": "Generation is taking longer than usual. If the task doesn't finish, the money will be refunded "
                   "automatically.",
        "failed": "Generation failed. You were not charged",
        "variant": "Variant {n}\nHeadline: {title}\nText: {text}\nCall to action: {cta}",
        "variants_header": "Done:",
        "variants_footer": "Copy the variant you like into the campaign form in your dashboard.",
        "default_audience": "General audience",
        "cancelled": "Cancelled.",
        "unknown": "I didn't understand the command.",
        "api_unavailable": "The platform is unavailable right now — please try again later",
        "api_timeout": "The platform didn't respond in time — please try again later",
        "api_error": "Error {status}",
        "api_server_error": "The platform server returned an error ({status})",
        "cmd_generate": "Write an ad",
        "cmd_balance": "Balance",
        "cmd_link": "Link account with a code",
        "cmd_unlink": "Unlink account",
        "cmd_help": "What the bot can do",
    },
    "de": {
        "help": (
            "Was der Bot kann:\n"
            "/generate — eine Anzeige schreiben (3 Varianten)\n"
            "/balance — Kontoguthaben\n"
            "/link CODE — Konto verknüpfen (der Code steht im Profil im Dashboard)\n"
            "/unlink — Konto trennen\n"
            "/cancel — Eingabe abbrechen"
        ),
        "ask_description": (
            "Beschreiben Sie Ihr Produkt oder Ihre Dienstleistung: was es ist, Vorteile, Preis, Besonderheiten "
            "(mindestens 10 Zeichen).\n"
            "Die Zielgruppe können Sie in einer neuen Zeile nach „Zielgruppe:“ angeben.\n\n"
            "/cancel — abbrechen"
        ),
        "not_linked": (
            "Telegram ist noch nicht mit einem Konto der Plattform verknüpft.\n\n"
            "1. Öffnen Sie Ihr Profil ({cabinet}) und klicken Sie auf „Verknüpfungscode anfordern“.\n"
            "2. Klicken Sie auf „Bot öffnen und verknüpfen“ oder senden Sie hierher: /link CODE"
        ),
        "cabinet": "Dashboard der Plattform",
        "hello": "Hallo! Konto: {email}",
        "hello_unlinked": "Hallo! Ich helfe Werbetreibenden auf der Ad Platform.",
        "link_usage": "Senden Sie den Code zusammen mit dem Befehl: /link CODE",
        "link_failed": "Verknüpfung fehlgeschlagen: {detail}",
        "linked": "Konto {email} verknüpft.\nGuthaben: {balance}",
        "balance": "Konto: {email}\nGuthaben: {balance}",
        "held": "Für Generierungen reserviert: {amount}",
        "unlinked": "Konto getrennt. Erneut verknüpfen — mit dem Code aus Ihrem Profil ({cabinet})",
        "already_unlinked": "Telegram ist ohnehin nicht verknüpft.",
        "too_short": "Beschreiben Sie es genauer — mindestens {count} Zeichen.",
        "progress": "Ich schreibe die Anzeige… Während der Generierung sind {amount} reserviert, abgerechnet wird "
                    "nach tatsächlichem Verbrauch.",
        "result_error": "Ergebnis konnte nicht abgerufen werden: {detail}",
        "timeout": "Die Generierung dauert länger als üblich. Wird die Aufgabe nicht abgeschlossen, wird das Geld "
                   "automatisch erstattet.",
        "failed": "Generierung fehlgeschlagen. Es wurde nichts abgebucht",
        "variant": "Variante {n}\nTitel: {title}\nText: {text}\nHandlungsaufforderung: {cta}",
        "variants_header": "Fertig:",
        "variants_footer": "Kopieren Sie die gewünschte Variante in das Kampagnenformular im Dashboard.",
        "default_audience": "Allgemeines Publikum",
        "cancelled": "Abgebrochen.",
        "unknown": "Diesen Befehl habe ich nicht verstanden.",
        "api_unavailable": "Die Plattform ist gerade nicht erreichbar — versuchen Sie es später erneut",
        "api_timeout": "Die Plattform hat nicht rechtzeitig geantwortet — versuchen Sie es später erneut",
        "api_error": "Fehler {status}",
        "api_server_error": "Der Server der Plattform hat einen Fehler gemeldet ({status})",
        "cmd_generate": "Anzeige schreiben",
        "cmd_balance": "Guthaben",
        "cmd_link": "Konto mit Code verknüpfen",
        "cmd_unlink": "Konto trennen",
        "cmd_help": "Was der Bot kann",
    },
}


def language_of(code: str | None) -> str:
    """language_code из Telegram («de», «pt-br», None) → язык бота."""
    lang = (code or "").lower().split("-")[0]
    return lang if lang in LANGUAGES else DEFAULT_LANGUAGE


class Texts:
    """Тексты на языке пользователя: t("balance", email=…, balance=…)."""

    def __init__(self, lang: str):
        self.lang = lang if lang in LANGUAGES else DEFAULT_LANGUAGE
        self._messages = MESSAGES[self.lang]

    def __call__(self, key: str, **values) -> str:
        return self._messages[key].format(**values) if values else self._messages[key]

    def money(self, value: float) -> str:
        text = f"{value:,.2f}"  # 1,234.50
        if self.lang == "ru":
            return text.replace(",", " ").replace(".", ",")  # 1 234,50
        if self.lang == "de":
            return text.replace(",", " ").replace(".", ",").replace(" ", ".")  # 1.234,50
        return text

    def cabinet(self, site_url: str) -> str:
        return f"{site_url}/app" if site_url else self("cabinet")

    def not_linked(self, site_url: str) -> str:
        return self("not_linked", cabinet=self.cabinet(site_url))

    def variants(self, items: list[dict]) -> str:
        blocks = [self("variant", n=i, title=v["title"], text=v["text"], cta=v["cta"])
                  for i, v in enumerate(items, start=1)]
        return f"{self('variants_header')}\n\n" + "\n\n".join(blocks) + f"\n\n{self('variants_footer')}"
