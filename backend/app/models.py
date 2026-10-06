import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON, BigInteger, CheckConstraint, Date, DateTime, Enum, ForeignKey,
    Index, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

Money = Numeric(12, 2)


class UserRole(str, enum.Enum):
    ADVERTISER = "advertiser"
    ADMIN = "admin"


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    MODERATION = "moderation"
    READY_TO_PAY = "ready_to_pay"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    REJECTED = "rejected"


def _enum(cls, name):
    # Храним значения ("advertiser"), а не имена ("ADVERTISER")
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e],
                native_enum=False, create_constraint=True, validate_strings=True)


class TransactionType(str, enum.Enum):
    DEPOSIT = "deposit"          # Пополнение баланса
    CLICK_SPEND = "click_spend"  # Списание за клик
    REFUND = "refund"            # Возврат средств
    AI_SPEND = "ai_spend"        # Оплата AI-генерации (копирайтер)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_users_balance_nonneg"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    role: Mapped[UserRole] = mapped_column(
        _enum(UserRole, "user_role"), default=UserRole.ADVERTISER,
        server_default=UserRole.ADVERTISER.value)
    balance: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")
    # Число записей в журнале транзакций — total для истории без COUNT(*) (см. app/ledger.py)
    transactions_count: Mapped[int] = mapped_column(default=0, server_default="0")
    # Версия токенов: смена пароля увеличивает её, и все ранее выданные токены перестают действовать
    token_version: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    campaigns: Mapped[list["Campaign"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", passive_deletes=True)

    # Флаг администратора — производный от role, а не отдельная колонка: иначе у пользователя
    # было бы два независимых признака админа, которые могут разойтись (role=advertiser,
    # is_admin=True) — и проверки доступа противоречили бы друг другу.
    # Работает и чтение, и запись, и User(is_admin=True), и запросы User.is_admin == True
    @hybrid_property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @is_admin.inplace.setter
    def _is_admin_setter(self, value: bool) -> None:
        self.role = UserRole.ADMIN if value else UserRole.ADVERTISER

    @is_admin.inplace.expression
    @classmethod
    def _is_admin_expression(cls):
        return cls.role == UserRole.ADMIN


class Placement(Base):
    __tablename__ = "placements"
    __table_args__ = (
        CheckConstraint("price_per_day >= 0 AND price_per_click >= 0",
                        name="ck_placements_prices_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    code_identifier: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    price_per_day: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")
    price_per_click: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")
    is_active: Mapped[bool] = mapped_column(default=True, server_default="1")

    campaigns: Mapped[list["Campaign"]] = relationship(back_populates="placement")


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint("end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
                        name="ck_campaigns_dates"),
        # /serve: активные кампании конкретной площадки. Покрывает и FK placement_id
        Index("ix_campaigns_placement_status", "placement_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    placement_id: Mapped[int] = mapped_column(
        ForeignKey("placements.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(2048))
    target_url: Mapped[str] = mapped_column(String(2048))
    status: Mapped[CampaignStatus] = mapped_column(
        _enum(CampaignStatus, "campaign_status"), default=CampaignStatus.DRAFT,
        server_default=CampaignStatus.DRAFT.value, index=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    # AI-проверка (app/ai.py) — подсказка модератору; при каждой отправке на модерацию сбрасывается
    ai_verdict: Mapped[str | None] = mapped_column(String(20))   # approve | review | reject | error
    ai_risk: Mapped[str | None] = mapped_column(String(10))      # low | medium | high
    ai_reasons: Mapped[list | None] = mapped_column(JSON)
    ai_summary: Mapped[str | None] = mapped_column(Text)         # итог или текст ошибки проверки
    ai_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Показы и оплаченные клики (повторные и от ботов не считаются)
    impressions_count: Mapped[int] = mapped_column(default=0, server_default="0")
    clicks_count: Mapped[int] = mapped_column(default=0, server_default="0")
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner: Mapped["User"] = relationship(back_populates="campaigns")
    placement: Mapped["Placement"] = relationship(back_populates="campaigns")

    # Для CampaignAdminResponse (загружать с selectinload, чтобы не было запроса на каждую строку)
    @property
    def owner_email(self) -> str:
        return self.owner.email

    @property
    def placement_name(self) -> str:
        return self.placement.name


class Click(Base):
    """Оплаченный клик. По (кампания, IP, минута) отсекаются повторные клики."""
    __tablename__ = "clicks"
    __table_args__ = (
        UniqueConstraint("campaign_id", "ip_hash", "time_window", name="uq_clicks_dedup"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Отдельный индекс не нужен: uq_clicks_dedup начинается с campaign_id
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    # HMAC от IP: повтор распознаём, а сам IP (персональные данные) не храним
    ip_hash: Mapped[str] = mapped_column(String(64))
    # Минута клика: unix_time // 60. Целое число — сравнение «за последние N минут»
    # одинаково работает в SQLite и PostgreSQL. Уникальность по минуте ловит одновременные
    # повторы, а проверка в _charge_click — повторы в течение CLICK_DEDUP_MINUTES
    time_window: Mapped[int] = mapped_column(BigInteger)
    cost: Mapped[Decimal] = mapped_column(Money)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class Transaction(Base):
    """Журнал движения денег. Баланс пользователя = сумма пополнений и возвратов − сумма списаний."""
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),
        # /wallet/history: WHERE user_id = ? ORDER BY id DESC — без сортировки в памяти
        Index("ix_transactions_user_id_id", "user_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # RESTRICT: финансовую историю нельзя потерять вместе с пользователем
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    amount: Mapped[Decimal] = mapped_column(Money)  # Всегда положительное, направление задаёт type
    type: Mapped[TransactionType] = mapped_column(_enum(TransactionType, "transaction_type"))
    # За какую кампанию списание (для CLICK_SPEND); при удалении кампании запись остаётся
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), index=True)
    description: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class CampaignDailyStat(Base):
    """Статистика кампании за день (UTC): показы, оплаченные клики, расход."""
    __tablename__ = "campaign_daily_stats"

    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    impressions: Mapped[int] = mapped_column(default=0, server_default="0")
    clicks: Mapped[int] = mapped_column(default=0, server_default="0")
    spend: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")


class AuthAttempt(Base):
    """Попытки входа/регистрации для ограничения частоты (см. app/ratelimit.py)."""
    __tablename__ = "auth_attempts"
    __table_args__ = (
        Index("ix_auth_attempts_kind_key_ts", "kind", "key", "ts"),
        Index("ix_auth_attempts_ts", "ts"),  # для очистки старых записей
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # login_email, login_ip, register_ip
    key: Mapped[str] = mapped_column(String(64))    # HMAC от email/IP — сами значения не храним
    ts: Mapped[int] = mapped_column(BigInteger)     # unix-время, сек


class AILog(Base):
    """Журнал AI-запросов: модель, токены, себестоимость и списанная сумма."""
    __tablename__ = "ai_logs"
    __table_args__ = (Index("ix_ai_logs_user_id_id", "user_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    prompt_type: Mapped[str] = mapped_column(String(50))  # gemini_ad_copy
    model: Mapped[str] = mapped_column(String(100))
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)  # включая «размышления» модели
    total_tokens: Mapped[int] = mapped_column(default=0)
    # Стоимость в $ с наценкой (6 знаков) и сумма, списанная с баланса (в валюте баланса, до копеек)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    charged: Mapped[Decimal] = mapped_column(Money)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
