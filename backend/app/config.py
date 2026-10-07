import os
import secrets
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
# Служебные данные приложения (сгенерированный ключ). В Docker — том /app/data
DATA_DIR = Path(os.environ.get("DATA_DIR") or BASE_DIR / "data")


def _load_or_create_secret_key() -> str:
    """Ключ из DATA_DIR/secret_key; при первом запуске — случайный, сохраняется туда же.

    Страховка на случай потери .env: платформа не падает и не переходит на известный всем
    ключ из примеров — у каждой установки свой, и после перезапуска он тот же.
    """
    path = DATA_DIR / "secret_key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Пишем во временный файл и атомарно «публикуем» через os.link: если несколько процессов
    # сервера стартуют одновременно, ключ создаст первый, остальные прочитают его же
    # (иначе у процессов были бы разные ключи и токены «терялись» бы между ними)
    tmp = path.with_name(f".secret_key.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    try:
        tmp.chmod(0o600)  # читать может только владелец (на Windows игнорируется)
    except OSError:
        pass
    try:
        os.link(tmp, path)
    except FileExistsError:
        pass  # другой процесс успел первым — берём его ключ
    finally:
        tmp.unlink(missing_ok=True)
    return path.read_text(encoding="utf-8").strip()


class Settings(BaseSettings):
    # По умолчанию SQLite-файл backend/app.db (не зависит от текущей папки).
    # Для PostgreSQL задайте в backend/.env:
    # DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/ad_db
    database_url: str = f"sqlite:///{(BASE_DIR / 'app.db').as_posix()}"

    # Ключ подписи токенов: из SECRET_KEY (backend/.env), а если не задан — сгенерированный
    # и сохранённый в DATA_DIR/secret_key. В коде и примерах ключ не хранится
    secret_key: str = ""
    # ALGORITHM — имя из учебной инструкции, то же самое
    jwt_algorithm: str = Field(default="HS256", validation_alias=AliasChoices("JWT_ALGORITHM", "ALGORITHM"))
    access_token_expire_minutes: int = 60 * 24  # Токен валиден 24 часа
    # Стоимость bcrypt: 12 ≈ 0.25 с на хеш — защита от перебора. В тестах ставим 4
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)

    # Защита от перебора паролей и массовой регистрации
    login_max_failures_per_email: int = Field(default=5, ge=1)
    login_max_failures_per_ip: int = Field(default=20, ge=1)
    login_window_minutes: int = Field(default=15, ge=1)
    register_max_per_ip_per_hour: int = Field(default=10, ge=1)

    # AI-проверка объявлений (Claude). Нет ключа — проверка выключена, модерация только ручная
    anthropic_api_key: str = ""
    ai_model: str = "claude-opus-5-5"
    # Кто проверяет объявления и сайты: auto — Claude, если задан ANTHROPIC_API_KEY, иначе Gemini
    # (GEMINI_API_KEY, модель GEMINI_MODEL); anthropic / gemini — только он
    ai_moderation_provider: Literal["auto", "anthropic", "gemini"] = "auto"
    ai_timeout_seconds: float = Field(default=30, gt=0)
    # Автопилот модерации (нужен ANTHROPIC_API_KEY). Модель уверенно нашла нарушение (reject + high) —
    # кампания отклоняется сама; уверенно одобрила (approve + low) и стоп-фразы не нашлись — запускается
    # сама. Остальное (сомнения, сбой модели) — модератору. false — решает только человек
    ai_auto_reject: bool = True
    ai_auto_approve: bool = True
    # Ключ Google Gemini API (Google AI Studio, aistudio.google.com/apikey)
    gemini_api_key: str = ""
    # AI-копирайтер. gemini-1.5-flash из инструкции Google отключил; 3.8 Flash — рекомендованная сейчас
    gemini_model: str = "gemini-3.8-flash"
    # Цены модели, $ за 1 млн токенов (ai.google.dev/gemini-api/docs/pricing). Для 3.8 Flash
    # до 31.12.2026 — 0.75 / 3.75, с 01.01.2027 — 1.50 / 7.50: обновите при смене цен или модели
    gemini_price_input_per_1m: Decimal = Field(default=Decimal("0.75"), ge=0)
    gemini_price_output_per_1m: Decimal = Field(default=Decimal("3.75"), ge=0)  # включая «размышления»
    ai_markup: Decimal = Field(default=Decimal("1.5"), ge=1)  # наценка платформы к себестоимости
    # Резервная модель копирайтера: при сбое Gemini (лимит, недоступность, таймаут) — Claude.
    # Работает, если задан ANTHROPIC_API_KEY. Haiku 4.5 — быстрая и дешёвая, для коротких текстов хватает
    ai_copy_fallback: bool = True
    claude_copy_model: str = "claude-haiku-4-5"
    # Цены, $ за 1 млн токенов (platform.claude.com/docs/en/about-claude/pricing): Haiku 4.5 — 1 / 5
    claude_price_input_per_1m: Decimal = Field(default=Decimal("1"), ge=0)
    claude_price_output_per_1m: Decimal = Field(default=Decimal("5"), ge=0)
    # OpenAI Moderation API (бесплатный, но нужен ключ OpenAI): нет ключа — только локальный фильтр
    openai_api_key: str = ""
    openai_moderation_model: str = "omni-moderation-latest"
    # Сколько единиц валюты баланса стоит 1 $ (баланс в долларах — 1, в рублях — курс, например 90)
    usd_rate: Decimal = Field(default=Decimal("1"), gt=0)

    # Очистка «зависших» AI-задач (с возвратом резерва): как часто проверять и какую задачу
    # считать зависшей. Порог — не меньше 2 минут, иначе закрывались бы работающие задачи
    ai_cleanup_interval_seconds: int = Field(default=300, ge=10)
    ai_task_timeout_minutes: int = Field(default=10, ge=2)

    # Sentry — оповещения об ошибках (sentry.io → проект → Client Keys (DSN)). Пусто — выключено
    sentry_dsn: str = ""
    sentry_environment: str = "production"
    # Доля запросов с замером производительности (0 — только ошибки; 0.1 — каждый десятый запрос)
    sentry_traces_sample_rate: float = Field(default=0.0, ge=0, le=1)

    # Telegram-бот (папка bot/). Общий секрет бота и API (не короче 32 символов): бот передаёт его
    # в заголовке X-Bot-Secret. Пусто — бот выключен, эндпоинты /api/v1/bot/* не существуют (404)
    telegram_bot_secret: str = ""
    # Имя бота без @ — для ссылки привязки t.me/<имя>?start=<код> в кабинете
    telegram_bot_username: str = ""
    telegram_link_code_minutes: int = Field(default=10, ge=1, le=60)

    # Приём платежей (app/payments). Пусто — выключен: баланс пополняет администратор.
    # "test" — тестовый провайдер для разработки (страница «Оплатить/Отменить», деньги НЕнастоящие).
    # Настоящие провайдеры (yookassa, stripe…) добавляются классом в app/payments/ — см. README
    payments_provider: str = ""
    payments_currency: str = Field(default="RUB", min_length=3, max_length=3)
    payments_min_amount: Decimal = Field(default=Decimal("1"), gt=0)
    payments_max_amount: Decimal = Field(default=Decimal("100000"), gt=0)
    # Секрет подписи уведомлений тестового провайдера (пусто — SECRET_KEY)
    payments_test_secret: str = ""
    # Адрес сайта для возврата после оплаты (https://ads.example.com); пусто — адрес из запроса
    public_url: str = ""

    # Почта (восстановление пароля, app/services/mailer.py). SMTP_HOST пусто — письма не отправляются,
    # а пишутся в журнал сервера (для проверки локально). Порт 465 — SMTPS, иначе STARTTLS
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""  # адрес отправителя; пусто — SMTP_USER
    smtp_starttls: bool = True
    # Сколько минут действует ссылка для смены пароля
    password_reset_minutes: int = Field(default=30, ge=5, le=24 * 60)

    # Уведомления по email (app/services/notify.py): решения по кампаниям, сайтам, выплатам; «заканчиваются
    # деньги» — когда баланс рекламодателя с активными кампаниями ниже LOW_BALANCE_THRESHOLD; ежедневная
    # сводка администраторам — только если что-то ждёт решения. Нужен SMTP (иначе письма — в журнал)
    notifications: bool = True
    low_balance_threshold: Decimal = Field(default=Decimal("5"), ge=0)
    admin_digest: bool = True

    # Документация API (/docs, /redoc, /openapi.json). На боевом сервере лучше выключить: это полный список
    # адресов, включая административные (их всё равно защищает вход администратора, но раскрывать незачем)
    api_docs: bool = True

    # Redis — брокер очереди Celery (фоновые задачи). В Docker адрес задаёт docker-compose
    redis_url: str = "redis://localhost:6379/0"

    # Удаление старых служебных записей раз в сутки (app/maintenance.py). Деньги и журналы не удаляются
    auto_purge: bool = True
    clicks_retention_days: int = Field(default=30, ge=1)
    ai_tasks_retention_days: int = Field(default=90, ge=1)

    # Повторный клик с того же IP по той же кампании в этом окне не оплачивается
    click_dedup_minutes: int = Field(default=10, gt=0)

    # Партнёрская программа (app/services/partners.py).
    # Доля владельца сайта от цены клика; у отдельного сайта администратор может задать свою
    publisher_revenue_share: Decimal = Field(default=Decimal("0.60"), ge=0, le=1)
    # Сколько дней заработок «созревает», прежде чем его можно вывести: время найти накрутку
    earnings_hold_days: int = Field(default=14, ge=1, le=180)
    # Минимальная сумма заявки на выплату (в валюте баланса). Перевод на рекламный баланс — без минимума
    payout_min_amount: Decimal = Field(default=Decimal("20"), gt=0)
    # Цена клика для новой площадки партнёра (администратор может изменить у площадки)
    partner_default_cpc: Decimal = Field(default=Decimal("0.10"), ge=0)
    # Реферальная программа: доля дохода платформы (после доли партнёра-сайта) с приглашённого
    # пользователя — пригласившему, и сколько дней с регистрации приглашённого она начисляется
    referral_share: Decimal = Field(default=Decimal("0.10"), ge=0, le=Decimal("0.5"))
    referral_days: int = Field(default=365, ge=1, le=3650)
    # Раз в сутки блокировать сайты с 2+ признаками накрутки и аннулировать созревающий заработок владельца
    fraud_auto_block: bool = True
    # Автоматическая проверка новых сайтов партнёров (открывается ли, оценка ИИ) — app/services/site_check.py.
    # Решение без администратора — по тем же правилам AI_AUTO_APPROVE / AI_AUTO_REJECT
    site_auto_check: bool = True
    # Аукцион показов: доля показов случайной кампании (остальные — с наибольшим «ставка × CTR»).
    # Без неё новая кампания без кликов никогда не набрала бы статистику и не попала бы в показ
    auction_explore_rate: float = Field(default=0.1, ge=0, le=1)
    # Сколько площадок (мест под баннер) партнёр может создать на одном сайте
    partner_max_placements_per_site: int = Field(default=20, ge=1)

    # С каких доменов фронтенд (React/Vue/Next.js) может обращаться к API из браузера,
    # через запятую. По умолчанию — локальные серверы разработки. На сервере укажите домен
    # фронтенда: CORS_ORIGINS=https://app.example.com. "*" — с любых (не рекомендуется).
    # Выдача рекламы (/api/v1/ad/*, /widget.js) доступна с любых сайтов независимо от этого.
    cors_origins: str = ",".join(
        f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (3000, 5173, 8080)
    )

    # Допустимые значения заголовка Host через запятую, например "ads.example.com".
    # "*" — любые (по умолчанию). На сервере защищает от подмены Host в ссылках клика
    allowed_hosts: str = "*"

    @property
    def GEMINI_API_KEY(self) -> str:  # noqa: N802 — имя из учебной инструкции, то же, что gemini_api_key
        return self.gemini_api_key

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        hosts = [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]
        if hosts and "*" not in hosts:
            # Внутренние адреса — всегда: по ним ходит проверка здоровья контейнера (HEALTHCHECK).
            # Снаружи по ним не обратиться: прокси принимает запросы только для своего домена
            hosts += [h for h in ("127.0.0.1", "localhost") if h not in hosts]
        return hosts

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore", populate_by_name=True)

    @model_validator(mode="after")
    def _secret_key(self) -> "Settings":
        if not self.secret_key.strip():
            self.secret_key = _load_or_create_secret_key()
        if len(self.secret_key) < 32:
            raise ValueError("SECRET_KEY должен быть не короче 32 символов")
        # Секрет бота даёт право действовать от имени любого привязавшего Telegram пользователя
        if self.telegram_bot_secret and len(self.telegram_bot_secret) < 32:
            raise ValueError("TELEGRAM_BOT_SECRET должен быть не короче 32 символов (или пусто — бот выключен)")
        return self


settings = Settings()
