import enum
import uuid
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
    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_users_balance_nonneg"),
        CheckConstraint("held_balance >= 0", name="ck_users_held_balance_nonneg"),
        CheckConstraint("earnings_balance >= 0", name="ck_users_earnings_balance_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    role: Mapped[UserRole] = mapped_column(
        _enum(UserRole, "user_role"), default=UserRole.ADVERTISER,
        server_default=UserRole.ADVERTISER.value)
    # Доступный баланс: им оплачиваются клики и AI-генерация
    balance: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")
    # Замороженная сумма: резерв под AI-генерации, которые ещё выполняются (app/services/ai_billing.py).
    # В balance она уже не входит; после генерации — списывается по факту или возвращается в balance
    held_balance: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")
    # Заработок партнёра, доступный к выводу (уже «созрел» — см. app/services/partners.py).
    # Отдельно от balance: рекламный баланс тратится на клики, а этот — выплачивается партнёру
    earnings_balance: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")
    # Реферальная программа: свой код (выдаётся при первом запросе ссылки) и кто пригласил.
    # Пригласивший получает REFERRAL_SHARE дохода платформы с приглашённого REFERRAL_DAYS дней
    referral_code: Mapped[str | None] = mapped_column(String(16), unique=True)
    referred_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True)
    # Число записей в журнале транзакций — total для истории без COUNT(*) (см. app/ledger.py)
    transactions_count: Mapped[int] = mapped_column(default=0, server_default="0")
    # Версия токенов: смена пароля увеличивает её, и все ранее выданные токены перестают действовать
    token_version: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    campaigns: Mapped[list["Campaign"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", passive_deletes=True)

    @property
    def telegram_linked(self) -> bool:
        """Привязан ли Telegram (сам telegram_id в ответах API не отдаём)."""
        return self.telegram_id is not None

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
    # Сайт партнёра, на котором стоит площадка. NULL — площадка самой платформы (весь доход — платформе)
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id", ondelete="RESTRICT"), index=True)

    # passive_deletes="all": при удалении площадки ORM не обнуляет placement_id у кампаний
    # (иначе они молча стали бы кампаниями на всю сеть) — удаление запрещает RESTRICT в БД
    campaigns: Mapped[list["Campaign"]] = relationship(back_populates="placement", passive_deletes="all")
    site: Mapped["Site | None"] = relationship(back_populates="placements")


class SiteStatus(str, enum.Enum):
    PENDING = "pending"    # ждёт проверки администратором
    APPROVED = "approved"  # реклама показывается, партнёр зарабатывает
    REJECTED = "rejected"  # не прошёл проверку (причина — в rejection_reason)
    BLOCKED = "blocked"    # заблокирован после одобрения (накрутка, запрещённый контент)


class Site(Base):
    """Сайт партнёра (владельца площадок). Реклама на его площадках идёт только после одобрения."""
    __tablename__ = "sites"
    __table_args__ = (
        CheckConstraint("revenue_share IS NULL OR (revenue_share >= 0 AND revenue_share <= 1)",
                        name="ck_sites_revenue_share"),
        Index("ix_sites_user_id_id", "user_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(2048))
    # Домен без www — один сайт не может принадлежать двум партнёрам
    domain: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[SiteStatus] = mapped_column(
        _enum(SiteStatus, "site_status"), default=SiteStatus.PENDING,
        server_default=SiteStatus.PENDING.value, index=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    # Доля партнёра от цены клика (0.6 = 60%). NULL — общая из настроек (PUBLISHER_REVENUE_SHARE)
    revenue_share: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    owner: Mapped["User"] = relationship()
    placements: Mapped[list["Placement"]] = relationship(back_populates="site")

    @property
    def owner_email(self) -> str:
        return self.owner.email

    @property
    def custom_share(self) -> bool:
        return self.revenue_share is not None

    @property
    def effective_share(self) -> Decimal:
        """Доля партнёра, по которой сейчас начисляется заработок."""
        from app.config import settings
        return settings.publisher_revenue_share if self.revenue_share is None else self.revenue_share


class SiteDailyStat(Base):
    """Статистика сайта партнёра за день (UTC): показы, клики, оборот (цена кликов) и доля партнёра."""
    __tablename__ = "site_daily_stats"

    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    impressions: Mapped[int] = mapped_column(default=0, server_default="0")
    clicks: Mapped[int] = mapped_column(default=0, server_default="0")
    revenue: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")
    earnings: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")


class EarningSource(str, enum.Enum):
    SITE = "site"          # доля от кликов на сайтах партнёра
    REFERRAL = "referral"  # реферальное вознаграждение


class PartnerEarning(Base):
    """Начисления партнёру за день. Пока matured = false, деньги «созревают» (EARNINGS_HOLD_DAYS):
    за это время накрутку можно найти и не платить за неё. Созревшие переходят в users.earnings_balance."""
    __tablename__ = "partner_earnings"
    __table_args__ = (
        Index("ix_partner_earnings_matured_day", "matured", "day"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[EarningSource] = mapped_column(_enum(EarningSource, "earning_source"), primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")
    matured: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Аннулировано администратором (накрутка): сумма переносится сюда из amount и не выплачивается.
    # Сумма, а не флаг: клики на честных сайтах того же партнёра в тот же день продолжают начисляться в amount
    forfeited: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"), server_default="0")


class PartnerTxType(str, enum.Enum):
    EARNING = "earning"              # созревший заработок зачислен к выводу
    PAYOUT = "payout"                # заявка на выплату (сумма списана с заработка)
    PAYOUT_RETURN = "payout_return"  # заявка отклонена — сумма вернулась
    TO_BALANCE = "to_balance"        # переведено на рекламный баланс


class PartnerTransaction(Base):
    """Журнал заработка партнёра: earnings_balance = earning + payout_return − payout − to_balance."""
    __tablename__ = "partner_transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_partner_transactions_amount_positive"),
        Index("ix_partner_transactions_user_id_id", "user_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    amount: Mapped[Decimal] = mapped_column(Money)
    type: Mapped[PartnerTxType] = mapped_column(_enum(PartnerTxType, "partner_tx_type"))
    payout_id: Mapped[int | None] = mapped_column(ForeignKey("payouts.id", ondelete="SET NULL"))
    description: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class PayoutStatus(str, enum.Enum):
    PENDING = "pending"    # ждёт выплаты администратором
    PAID = "paid"          # деньги отправлены
    REJECTED = "rejected"  # отклонена, сумма вернулась на заработок


class Payout(Base):
    """Заявка партнёра на вывод заработка. Деньги переводит администратор вручную и отмечает заявку."""
    __tablename__ = "payouts"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payouts_amount_positive"),
        Index("ix_payouts_user_id_id", "user_id", "id"),
        Index("ix_payouts_status_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    amount: Mapped[Decimal] = mapped_column(Money)
    method: Mapped[str] = mapped_column(String(20))      # paypal, bank, card, crypto, other
    details: Mapped[str] = mapped_column(String(500))    # реквизиты, куда платить
    status: Mapped[PayoutStatus] = mapped_column(
        _enum(PayoutStatus, "payout_status"), default=PayoutStatus.PENDING,
        server_default=PayoutStatus.PENDING.value)
    admin_note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped["User"] = relationship()

    @property
    def owner_email(self) -> str:
        return self.owner.email


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint("end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
                        name="ck_campaigns_dates"),
        CheckConstraint("cpc_bid IS NULL OR cpc_bid > 0", name="ck_campaigns_cpc_bid_positive"),
        # /serve: активные кампании конкретной площадки. Покрывает и FK placement_id
        Index("ix_campaigns_placement_status", "placement_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Площадка кампании; NULL — вся сеть: показ на любой площадке, где ставка не ниже её цены клика
    placement_id: Mapped[int | None] = mapped_column(
        ForeignKey("placements.id", ondelete="RESTRICT"))
    # Ставка за клик. NULL — цена клика площадки (как до аукциона). Ставка ниже цены площадки —
    # на этой площадке кампания не участвует. Выше ставка — чаще показ (app/routers/ads.py)
    cpc_bid: Mapped[Decimal | None] = mapped_column(Money)
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
    placement: Mapped["Placement | None"] = relationship(back_populates="campaigns")

    # Для CampaignAdminResponse (загружать с selectinload, чтобы не было запроса на каждую строку)
    @property
    def owner_email(self) -> str:
        return self.owner.email

    @property
    def placement_name(self) -> str | None:
        """Название площадки; None — кампания на всю сеть."""
        return self.placement.name if self.placement is not None else None


class Click(Base):
    """Оплаченный клик. По (кампания, IP, минута) отсекаются повторные клики."""
    __tablename__ = "clicks"
    __table_args__ = (
        UniqueConstraint("campaign_id", "ip_hash", "time_window", name="uq_clicks_dedup"),
        # Отчёт о накрутке: клики площадки за последние дни
        Index("ix_clicks_placement_id_time_window", "placement_id", "time_window"),
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
    # Площадка, на которой кликнули (для отчёта о накрутке по сайтам партнёров); у старых записей — NULL
    placement_id: Mapped[int | None] = mapped_column(ForeignKey("placements.id", ondelete="SET NULL"))
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


class PlacementDailyStat(Base):
    """Статистика площадки за день (UTC): показы, клики и оборот (цена кликов) — по всем кампаниям,
    включая кампании на всю сеть (у них нет своей площадки, поэтому считать через кампании нельзя)."""
    __tablename__ = "placement_daily_stats"

    placement_id: Mapped[int] = mapped_column(
        ForeignKey("placements.id", ondelete="CASCADE"), primary_key=True)
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


class AITaskStatus(str, enum.Enum):
    PENDING = "pending"        # создана, ждёт обработчика
    PROCESSING = "processing"  # обработчик работает
    COMPLETED = "completed"    # готово, результат в result
    FAILED = "failed"          # ошибка, причина в error_message


class AITask(Base):
    """Фоновая AI-задача: клиент получает id сразу и опрашивает статус, не держа соединение."""
    __tablename__ = "ai_tasks"
    __table_args__ = (
        # Список задач пользователя без фильтра, новые сверху
        Index("ix_ai_tasks_user_id_created_at", "user_id", "created_at"),
        # Список с фильтром по статусу и подсчёт незавершённых задач (лимит на пользователя)
        Index("ix_ai_tasks_user_id_status_created_at", "user_id", "status", "created_at"),
        # Очистка зависших: status IN (pending, processing) AND updated_at < порог
        Index("ix_ai_tasks_status_updated_at", "status", "updated_at"),
    )

    # UUID строкой: id нельзя угадать перебором, как 1, 2, 3 (задачи — личные данные пользователя)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[AITaskStatus] = mapped_column(
        _enum(AITaskStatus, "ai_task_status"), default=AITaskStatus.PENDING,
        server_default=AITaskStatus.PENDING.value)
    result: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(String(1000))
    # Операция резерва денег (ai_spend): если задачу прервал перезапуск сервера, резерв по ней возвращается
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Когда статус менялся в последний раз: «зависшую» в processing задачу видно по давности
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TelegramLinkCode(Base):
    """Одноразовый код привязки Telegram к аккаунту: выдаётся в кабинете, вводится в боте.

    Хранится не сам код, а HMAC от него: утечка базы не даёт привязать чужой аккаунт.
    """
    __tablename__ = "telegram_link_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Один действующий код на пользователя: новый заменяет старый
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Plan(Base):
    """Тариф: цена, срок и набор возможностей. Что даёт каждая возможность — app/services/entitlements.py."""
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)  # starter, pro — в ссылках и API
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500))
    price: Mapped[Decimal] = mapped_column(Money)
    # Срок действия в днях; None — разовая покупка (например, пакет)
    period_days: Mapped[int | None] = mapped_column()
    # Возможности тарифа: {"ai_generations": 300, ...} — ключи и их смысл задаёт entitlements.py
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(default=True, server_default="1")
    sort_order: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (CheckConstraint("price >= 0", name="ck_plans_price_nonneg"),)


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    CANCELED = "canceled"   # отменена до окончания (возможности пропадают сразу)
    EXPIRED = "expired"     # закончился срок (выставляется при проверке — см. entitlements.py)


class Subscription(Base):
    """Тариф пользователя на срок. Активная — status=active и ends_at в будущем (или без срока)."""
    __tablename__ = "subscriptions"
    __table_args__ = (Index("ix_subscriptions_user_id_status", "user_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))
    status: Mapped[SubscriptionStatus] = mapped_column(
        _enum(SubscriptionStatus, "subscription_status"), default=SubscriptionStatus.ACTIVE,
        server_default=SubscriptionStatus.ACTIVE.value)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Снимок возможностей на момент покупки: правка тарифа не меняет уже оплаченное
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    payment_id: Mapped[str | None] = mapped_column(ForeignKey("payments.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentPurpose(str, enum.Enum):
    TOP_UP = "top_up"   # пополнение баланса
    PLAN = "plan"       # покупка тарифа


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"        # создан, ждём оплату
    SUCCEEDED = "succeeded"    # оплачен — деньги зачислены / тариф активирован
    CANCELED = "canceled"      # пользователь отменил или истёк срок оплаты
    FAILED = "failed"          # отказ банка
    REFUNDED = "refunded"      # возврат денег плательщику


class Payment(Base):
    """Платёж через платёжную систему (app/payments). Статус меняют только уведомления провайдера."""
    __tablename__ = "payments"
    __table_args__ = (
        # Один платёж провайдера — одна запись: повторное уведомление не создаст дубль
        UniqueConstraint("provider", "provider_payment_id", name="uq_payments_provider_id"),
        Index("ix_payments_user_id_created_at", "user_id", "created_at"),
        CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
    )

    # UUID строкой: номер платежа не угадать перебором
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))  # финансовая история
    provider: Mapped[str] = mapped_column(String(30))
    provider_payment_id: Mapped[str | None] = mapped_column(String(100))
    purpose: Mapped[PaymentPurpose] = mapped_column(_enum(PaymentPurpose, "payment_purpose"))
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[PaymentStatus] = mapped_column(
        _enum(PaymentStatus, "payment_status"), default=PaymentStatus.PENDING,
        server_default=PaymentStatus.PENDING.value)
    confirmation_url: Mapped[str | None] = mapped_column(String(2048))
    # Операция пополнения в журнале денег (для top_up после оплаты)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
