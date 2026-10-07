from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import (
    AfterValidator, BaseModel, ConfigDict, EmailStr, Field, HttpUrl, PlainSerializer, TypeAdapter,
    UrlConstraints, field_validator, model_validator,
)

from app.i18n import Language
from app.models import CampaignStatus, TransactionType, UserRole


def _check_bcrypt_limit(password: str) -> str:
    # bcrypt учитывает только 72 байта; кириллица занимает 2 байта на символ
    if len(password.encode("utf-8")) > 72:
        raise ValueError("Пароль слишком длинный (максимум 72 байта)")
    return password


Password = Annotated[str, Field(min_length=8), AfterValidator(_check_bcrypt_limit)]

def _money_json(value: Decimal) -> float:
    # Округляем Decimal до копеек и только потом отдаём числом: 12 значащих цифр
    # (максимум Numeric(12, 2)) float хранит без искажений, JSON получит 95.0, а не "95.00"
    return float(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# Деньги в ответах API. Внутри — Decimal (без ошибок вида 0.1 + 0.2), в JSON — число:
# фронтенду не нужно превращать строку в число перед .toFixed(2) и расчётами
Money = Annotated[Decimal, PlainSerializer(_money_json, return_type=float, when_used="json")]

# SQLite не хранит часовой пояс: время из БД всегда в UTC, помечаем это явно,
# иначе браузер примет "2026-10-05T13:59:43" за местное время
UtcDatetime = Annotated[
    datetime, AfterValidator(lambda v: v if v.tzinfo else v.replace(tzinfo=timezone.utc))
]

# Email целиком в нижнем регистре, чтобы Test@mail.ru и test@mail.ru не были разными аккаунтами
NormalizedEmail = Annotated[EmailStr, AfterValidator(str.lower)]


# --- Схемы Пользователя ---
class UserCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"email": "user@example.com", "password": "password123"}]
    })

    email: NormalizedEmail
    password: Password


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: Password


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: UserRole
    is_admin: bool = False  # то же, что role == "admin" — удобно фронтенду
    balance: Money
    held_balance: Money = Decimal("0")  # заморожено под выполняющиеся AI-генерации
    telegram_linked: bool = False
    created_at: UtcDatetime


# --- Схемы Токена Авторизации ---
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    email: str | None = None


# --- Схемы Рекламных Мест (Placements) ---
# Цена: не отрицательная, не больше 2 знаков после запятой (как Numeric(12, 2) в БД)
Price = Annotated[Money, Field(ge=0, max_digits=12, decimal_places=2)]


class PlacementBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # Идентификатор для встраивания на сайт: латиница, цифры, "_" и "-"
    code_identifier: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    price_per_day: Price = Decimal("0")
    price_per_click: Price = Decimal("0")
    is_active: bool = True


class PlacementCreate(PlacementBase):
    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "name": "Баннер в шапке",
            "code_identifier": "header_banner_1",
            "price_per_day": 100.0,
            "price_per_click": 5.0,
            "is_active": True,
        }]
    })


class PlacementUpdate(BaseModel):
    """Частичное изменение площадки. code_identifier не меняется: он уже вставлен на сайтах."""
    model_config = ConfigDict(json_schema_extra={"examples": [{"price_per_click": 7.5}, {"is_active": False}]})

    name: str | None = Field(default=None, min_length=1, max_length=255)
    price_per_day: Price | None = None
    price_per_click: Price | None = None
    is_active: bool | None = None


class PlacementResponse(PlacementBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


# --- Схемы Кампаний (Campaigns) ---
# Только http/https: ссылка javascript:... в рекламе — это XSS у каждого, кто кликнет
_http_url = TypeAdapter(Annotated[HttpUrl, UrlConstraints(max_length=2048)])


def _check_http_url(url: str) -> str:
    return str(_http_url.validate_python(url))


WebUrl = Annotated[str, AfterValidator(_check_http_url)]


def _to_utc(v: datetime) -> datetime:
    # Без пояса — считаем UTC; с поясом — переводим в UTC
    return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)


# Дата/время от клиента. В БД всегда UTC: SQLite не хранит пояс, а /serve сравнивает с now() в UTC
UtcInput = Annotated[datetime, AfterValidator(_to_utc)]
Title = Annotated[str, Field(min_length=1, max_length=255)]
Description = Annotated[str, Field(max_length=1000)]


def _check_dates(start: datetime | None, end: datetime | None) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError("Дата окончания (end_date) раньше даты начала (start_date)")


class CampaignBase(BaseModel):
    placement_id: int = Field(gt=0)
    title: Title
    description: Description | None = None
    image_url: WebUrl | None = None
    target_url: WebUrl


class CampaignCreate(CampaignBase):
    # Период показа; не задан — показывается сразу и бессрочно
    start_date: UtcInput | None = None
    end_date: UtcInput | None = None

    @model_validator(mode="after")
    def _dates(self) -> "CampaignCreate":
        _check_dates(self.start_date, self.end_date)
        return self

    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "placement_id": 1,
            "title": "Акция на курсы по Python",
            "description": "Скидка 50% только на этой неделе!",
            "image_url": "https://example.com/banner.png",
            "target_url": "https://example.com/python-course",
        }]
    })


class CampaignResponse(CampaignBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    status: CampaignStatus
    rejection_reason: str | None = None
    impressions_count: int = 0
    clicks_count: int = 0
    start_date: UtcDatetime | None = None
    end_date: UtcDatetime | None = None
    created_at: UtcDatetime


class CampaignAdminResponse(CampaignResponse):
    """Для админа: кто владелец и на какой площадке — для очереди модерации."""
    owner_email: str
    placement_name: str
    # Подсказка AI-проверки (только для админа; рекламодатель видит лишь итог модерации)
    ai_verdict: Literal["approve", "review", "reject", "error"] | None = None
    ai_risk: Literal["low", "medium", "high"] | None = None
    ai_reasons: list[str] | None = None
    ai_summary: str | None = None
    ai_checked_at: UtcDatetime | None = None


class AIStatus(BaseModel):
    enabled: bool
    model: str
    auto_reject: bool


class CampaignUpdate(BaseModel):
    """Частичное изменение: передавайте только то, что меняется. null очищает поле."""
    model_config = ConfigDict(json_schema_extra={
        "examples": [{"title": "Новый заголовок"}, {"end_date": "2026-12-31T23:59:59Z"}]
    })

    placement_id: int | None = Field(default=None, gt=0)
    title: Title | None = None
    description: Description | None = None
    image_url: WebUrl | None = None
    target_url: WebUrl | None = None
    start_date: UtcInput | None = None
    end_date: UtcInput | None = None

    @model_validator(mode="after")
    def _required_not_null(self) -> "CampaignUpdate":
        # Эти поля можно не передавать, но нельзя очистить
        for name in ("placement_id", "title", "target_url"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"Поле {name} нельзя очистить")
        return self


# --- Схема для Модерации Кампании ---
class CampaignModerate(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {"status": "active"},
            {"status": "rejected", "rejection_reason": "Изображение не соответствует правилам площадки"},
        ]
    })

    # Модератор может только одобрить или отклонить
    status: Literal[CampaignStatus.ACTIVE, CampaignStatus.REJECTED]
    rejection_reason: str | None = Field(default=None, max_length=1000)

    @field_validator("rejection_reason")
    @classmethod
    def _strip_reason(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None

    @model_validator(mode="after")
    def _check_reason(self) -> "CampaignModerate":
        if self.status == CampaignStatus.REJECTED and not self.rejection_reason:
            raise ValueError("При отклонении кампании укажите причину (rejection_reason)")
        if self.status == CampaignStatus.ACTIVE:
            self.rejection_reason = None  # у одобренной кампании причины отказа нет
        return self


# --- Публичная выдача рекламы (для сайтов-партнёров) ---
class AdResponse(BaseModel):
    # Только то, что нужно для показа баннера: без владельца, статуса и target_url
    campaign_id: int
    title: str
    description: str | None = None
    image_url: str | None = None
    click_url: str  # ссылка через наш /click: клик засчитывается, потом редирект на сайт рекламодателя


# --- Схемы Кошелька (Wallet) ---
class DepositRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"amount": 1000}]})

    # Ограничиваем пополнение: больше 0 и не более 1 000 000 за раз, не больше 2 знаков
    # после запятой. Верхний предел защищает от опечаток и от переполнения Numeric(12, 2)
    amount: Decimal = Field(
        gt=0, le=1_000_000, max_digits=12, decimal_places=2,
        description="Сумма пополнения от 0.01 до 1,000,000",
    )


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    amount: Money  # Всегда положительное, направление задаёт type
    type: TransactionType
    campaign_id: int | None = None
    description: str | None = None
    created_at: UtcDatetime


class WalletBalanceResponse(BaseModel):
    balance: Money = Field(description="Доступно")
    held_balance: Money = Field(default=Decimal("0"), description="Заморожено под выполняющиеся AI-генерации")


# --- Пользователи (для админа) ---
class RoleUpdate(BaseModel):
    role: UserRole


# --- Статистика ---
class Totals(BaseModel):
    impressions: int
    clicks: int
    spend: Money
    ctr: float = Field(description="Клики / показы, %, 2 знака. 0 — если показов не было")


class DayStat(Totals):
    day: date


class CampaignTotals(Totals):
    campaign_id: int
    title: str
    status: CampaignStatus


class PlacementTotals(Totals):
    placement_id: int
    name: str
    code_identifier: str
    is_active: bool


class CampaignStats(BaseModel):
    campaign_id: int
    title: str
    status: CampaignStatus
    period_start: date
    period_end: date
    totals: Totals
    days: list[DayStat]


class MyStats(BaseModel):
    balance: Money
    period_start: date
    period_end: date
    campaigns_by_status: dict[str, int]
    totals: Totals
    days: list[DayStat]
    campaigns: list[CampaignTotals]
    campaigns_has_more: bool = False


class PlatformStats(BaseModel):
    period_start: date
    period_end: date
    totals: Totals = Field(description="spend — выручка платформы за период")
    days: list[DayStat]
    users_count: int
    active_campaigns: int
    moderation_queue: int
    advertisers_balance: Money
    placements: list[PlacementTotals]
    placements_has_more: bool = False


# Имена из учебной инструкции — синонимы схем статистики (/api/v1/analytics/summary)
AnalyticsTotals = Totals
DailyAnalytics = DayStat
CampaignAnalyticsItem = CampaignTotals
AnalyticsSummaryResponse = MyStats


# --- AI-копирайтер ---
class AdGenerateRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "product_description": "Онлайн-курс Python с нуля: 40 уроков, практика, сертификат",
        "target_audience": "Начинающие разработчики 18–30 лет",
    }]})

    product_description: str = Field(min_length=10, max_length=2000)
    target_audience: str = Field(default="Общая аудитория", min_length=1, max_length=300)
    language: Language = Field(default="ru", description="Язык объявлений: ru, en, de")

    @field_validator("product_description", "target_audience")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Поле не может быть пустым")
        return v


class AdCopyVariant(BaseModel):
    title: str
    text: str
    cta: str


class AdCopyContent(BaseModel):
    variants: list[AdCopyVariant]


class AdCopyBilling(BaseModel):
    tokens_used: int
    cost_deducted: Money = Field(description="Списано с баланса (в валюте баланса)")
    remaining_balance: Money


class AdCopyResponse(BaseModel):
    success: bool = True
    data: AdCopyContent
    billing: AdCopyBilling


class AICopywriterStatus(BaseModel):
    enabled: bool = Field(description="Копирайтер включён (задан GEMINI_API_KEY)")
    hold_amount: Money = Field(description="Сколько замораживается на одну генерацию (лишнее вернётся)")
    max_active_tasks: int


class AITaskCreated(BaseModel):
    task_id: str
    status: Literal["pending"] = "pending"
    check_status_url: str
    held_amount: Money = Field(description="Заморожено на балансе до завершения задачи")
    message: str = "Средства зарезервированы, задача запущена"


class AITaskResponse(BaseModel):
    task_id: str
    status: Literal["pending", "processing", "completed", "failed"]
    result: AdCopyContent | None = None
    error: str | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime


# --- Универсальный контейнер постраничной выдачи ---
T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Страница списка: PaginatedResponse[CampaignResponse] и т.п."""
    items: list[T]
    total: int = Field(..., ge=0, description="Общее количество записей в базе")
    limit: int = Field(..., ge=1, description="Размер страницы")
    offset: int = Field(..., ge=0, description="Смещение")


class AITaskListResponse(PaginatedResponse[AITaskResponse]):
    """Список AI-задач: общий формат списков (items, total, limit, offset) + номер страницы."""
    page: int = Field(..., ge=1, description="Номер страницы (с 1)")
    size: int = Field(..., ge=1, description="Размер страницы (то же, что limit)")


# --- Telegram-бот ---
class TelegramStatus(BaseModel):
    enabled: bool = Field(description="Бот подключён к платформе")
    bot_username: str | None = None
    linked: bool = Field(description="Telegram привязан к этому аккаунту")


class TelegramLinkCodeResponse(BaseModel):
    code: str = Field(description="Одноразовый код: отправьте боту /start <код>")
    expires_at: UtcDatetime
    deep_link: str | None = Field(default=None, description="Ссылка t.me/<бот>?start=<код> (если задано имя бота)")


TelegramId = Annotated[int, Field(gt=0, description="ID пользователя Telegram (message.from_user.id)")]


class BotLinkRequest(BaseModel):
    code: str = Field(min_length=4, max_length=32)
    telegram_id: TelegramId


class BotUserRequest(BaseModel):
    telegram_id: TelegramId


class BotGenerateRequest(AdGenerateRequest):
    telegram_id: TelegramId


class BotMe(BaseModel):
    email: str
    balance: Money
    held_balance: Money


# --- Платежи и тарифы ---
class PaymentsConfig(BaseModel):
    enabled: bool
    provider: str | None = None
    test_mode: bool = Field(description="Тестовый провайдер: деньги ненастоящие")
    currency: str
    min_amount: Money
    max_amount: Money


class TopUpRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, description="Сумма пополнения")


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    purpose: Literal["top_up", "plan"]
    status: Literal["pending", "succeeded", "canceled", "failed", "refunded"]
    amount: Money
    currency: str
    plan_id: int | None = None
    confirmation_url: str | None = Field(default=None, description="Куда отправить пользователя для оплаты")
    created_at: UtcDatetime
    paid_at: UtcDatetime | None = None


PlanCode = Annotated[str, Field(pattern=r"^[a-z0-9_-]{2,50}$", description="Латиница, цифры, _ и -")]


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None = None
    price: Money
    period_days: int | None = Field(default=None, description="Срок в днях; null — разовая покупка")
    features: dict = Field(default_factory=dict)
    is_active: bool = True
    sort_order: int = 0


class PlanCreate(BaseModel):
    code: PlanCode
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    period_days: int | None = Field(default=None, ge=1, le=3660)
    features: dict = Field(default_factory=dict)
    is_active: bool = True
    sort_order: int = 0


class PlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    period_days: int | None = Field(default=None, ge=1, le=3660)
    features: dict | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plan_id: int
    status: Literal["active", "canceled", "expired"]
    starts_at: UtcDatetime
    ends_at: UtcDatetime | None = None
    features: dict = Field(default_factory=dict)


class MyPlan(BaseModel):
    subscription: SubscriptionResponse | None = None
    plan: PlanResponse | None = None
    entitlements: dict = Field(description="Возможности сейчас: {'plan': код или null, ...}")


class PlanPurchaseResponse(BaseModel):
    payment: PaymentResponse | None = Field(default=None, description="Платный тариф — перейти по confirmation_url")
    subscription: SubscriptionResponse | None = Field(default=None, description="Бесплатный — активирован сразу")
