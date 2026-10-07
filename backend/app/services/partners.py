"""Партнёрская программа: доля владельца сайта от кликов, «созревание» заработка и выплаты.

Как движутся деньги партнёра:
1. Клик на его площадке: рекламодатель платит цену клика (app/routers/ads.py), доля партнёра
   (PUBLISHER_REVENUE_SHARE или своя у сайта) начисляется в partner_earnings за сегодня — accrue().
2. Через EARNINGS_HOLD_DAYS дней начисление «созревает» — mature(): сумма переходит
   в users.earnings_balance, в журнале partner_transactions — запись «earning».
   Пока деньги не созрели, накрутку можно найти и не платить за неё.
3. Созревшие деньги партнёр выводит заявкой (request_payout — администратор платит вручную)
   или переводит на свой рекламный баланс (transfer_to_balance — сразу).

Все изменения earnings_balance — атомарные UPDATE с условием в той же транзакции БД, что и запись
в журнале: баланс не уходит в минус при параллельных запросах и всегда сходится с журналом.
"""
import secrets
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import write_lock
from app.ledger import add_transaction
from app.models import (
    EarningSource, PartnerEarning, PartnerTransaction, PartnerTxType, Payout, PayoutStatus,
    SiteDailyStat, TransactionType, User,
)
from app.stats import utc_today

CENT = Decimal("0.01")


def _insert(db: Session):
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


def share_of(price: Decimal, rate: Decimal | None) -> Decimal:
    """Доля партнёра от цены клика, вниз до копейки: округление не в пользу партнёра,
    иначе платформа на каждом клике доплачивала бы долю копейки из своих."""
    rate = settings.publisher_revenue_share if rate is None else rate
    return (Decimal(price) * Decimal(rate)).quantize(CENT, rounding=ROUND_DOWN)


def bump_site_daily(db: Session, site_id: int, *, impressions: int = 0, clicks: int = 0,
                    revenue: Decimal = Decimal("0"), earnings: Decimal = Decimal("0")) -> None:
    """Атомарно прибавляет к статистике сайта за сегодня (как app/stats.bump_daily для кампаний)."""
    table = SiteDailyStat.__table__
    stmt = _insert(db)(table).values(site_id=site_id, day=utc_today(), impressions=impressions,
                                     clicks=clicks, revenue=revenue, earnings=earnings)
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.site_id, table.c.day],
        set_={name: table.c[name] + stmt.excluded[name]
              for name in ("impressions", "clicks", "revenue", "earnings")},
    )
    db.execute(stmt)


def accrue(db: Session, user_id: int, amount: Decimal, source: EarningSource) -> None:
    """Начисляет партнёру сумму за сегодня (созреет через EARNINGS_HOLD_DAYS). Без commit:
    выполняется в транзакции списания с рекламодателя — нет списания без начисления и наоборот."""
    if amount <= 0:
        return
    table = PartnerEarning.__table__
    stmt = _insert(db)(table).values(user_id=user_id, day=utc_today(), source=source.value,
                                     amount=amount, matured=False)
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.user_id, table.c.day, table.c.source],
        set_={"amount": table.c.amount + stmt.excluded.amount},
    )
    db.execute(stmt)


# ---------- Реферальная программа ----------
# Без похожих символов (0/O, 1/I/L): код диктуют и переписывают руками
REFERRAL_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def find_referrer(db: Session, code: str | None) -> int | None:
    """id владельца реферального кода; неизвестный или пустой код — None (регистрация не страдает)."""
    code = (code or "").strip().upper()
    if not code:
        return None
    return db.scalar(select(User.id).where(User.referral_code == code))


def referral_code(db: Session, user: User) -> str:
    """Код пользователя; при первом запросе — создаётся (случайный, 8 символов)."""
    while user.referral_code is None:
        code = "".join(secrets.choice(REFERRAL_ALPHABET) for _ in range(8))
        with write_lock():
            # Условие IS NULL: два параллельных первых запроса не перезапишут код друг друга
            db.execute(update(User).where(User.id == user.id, User.referral_code.is_(None))
                       .values(referral_code=code))
            try:
                db.commit()
            except IntegrityError:  # такой код уже у другого пользователя — пробуем другой
                db.rollback()
                continue
        db.refresh(user)
    return user.referral_code


def referral_since() -> datetime:
    """Приглашённые, зарегистрированные позже этого момента, ещё приносят вознаграждение."""
    return datetime.fromtimestamp(time.time(), tz=timezone.utc) - timedelta(days=settings.referral_days)


def accrue_referrals(db: Session, user_ids, platform_net: Decimal) -> None:
    """Вознаграждение пригласившим участников клика (рекламодателя и партнёра-сайта):
    REFERRAL_SHARE от дохода платформы с клика. Без commit — в транзакции списания за клик."""
    reward = share_of(platform_net, settings.referral_share) if platform_net > 0 else Decimal("0")
    if reward <= 0:
        return
    since = referral_since()
    for uid in dict.fromkeys(u for u in user_ids if u is not None):  # без повторов
        referrer = db.scalar(select(User.referred_by_id).where(
            User.id == uid, User.referred_by_id.is_not(None), User.created_at >= since))
        if referrer is not None:
            accrue(db, referrer, reward, EarningSource.REFERRAL)


def maturity_cutoff():
    """Последний день, начисления которого уже созрели. Сегодняшний не созревает никогда
    (EARNINGS_HOLD_DAYS ≥ 1): иначе клики после созревания дня добавились бы к уже зачисленной строке."""
    return utc_today() - timedelta(days=settings.earnings_hold_days)


def mature(db: Session, user_id: int | None = None) -> Decimal:
    """Переводит созревшие начисления в earnings_balance (одного партнёра или всех). Возвращает сумму.

    UPDATE ... RETURNING помечает строки и возвращает их суммы одной командой: два параллельных
    вызова (несколько процессов сервера) не зачислят одну строку дважды.
    """
    where = [PartnerEarning.matured.is_(False), PartnerEarning.day <= maturity_cutoff()]
    if user_id is not None:
        where.append(PartnerEarning.user_id == user_id)
    with write_lock():
        rows = db.execute(
            update(PartnerEarning).where(*where).values(matured=True)
            .returning(PartnerEarning.user_id, PartnerEarning.amount)
        ).all()
        totals: dict[int, Decimal] = {}
        for uid, amount in rows:
            totals[uid] = totals.get(uid, Decimal("0")) + amount
        for uid, amount in totals.items():
            if amount <= 0:
                continue
            db.execute(update(User).where(User.id == uid)
                       .values(earnings_balance=User.earnings_balance + amount))
            db.add(PartnerTransaction(user_id=uid, amount=amount, type=PartnerTxType.EARNING,
                                      description="Заработок партнёра доступен к выводу"))
        db.commit()
    return sum(totals.values(), Decimal("0"))


def pending_amount(db: Session, user_id: int) -> Decimal:
    """Сумма, которая ещё созревает."""
    return db.scalar(select(func.coalesce(func.sum(PartnerEarning.amount), 0)).where(
        PartnerEarning.user_id == user_id, PartnerEarning.matured.is_(False))) or Decimal("0")


def _take(db: Session, user_id: int, amount: Decimal) -> None:
    """Атомарно уменьшает earnings_balance; 400, если денег не хватает (транзакция откатывается)."""
    taken = db.execute(
        update(User).where(User.id == user_id, User.earnings_balance >= amount)
        .values(earnings_balance=User.earnings_balance - amount)
    ).rowcount
    if not taken:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Недостаточно заработка, доступного к выводу")


def request_payout(db: Session, user_id: int, amount: Decimal, method: str, details: str) -> Payout:
    if amount < settings.payout_min_amount:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Минимальная сумма выплаты — {settings.payout_min_amount:.2f}")
    mature(db, user_id)
    with write_lock():
        _take(db, user_id, amount)
        payout = Payout(user_id=user_id, amount=amount, method=method, details=details)
        db.add(payout)
        db.flush()
        db.add(PartnerTransaction(user_id=user_id, amount=amount, type=PartnerTxType.PAYOUT,
                                  payout_id=payout.id, description=f"Заявка на выплату #{payout.id}"))
        db.commit()
    db.refresh(payout)
    return payout


def transfer_to_balance(db: Session, user_id: int, amount: Decimal) -> None:
    """Созревший заработок → рекламный баланс того же пользователя (сразу, без минимума)."""
    mature(db, user_id)
    with write_lock():
        _take(db, user_id, amount)
        db.execute(update(User).where(User.id == user_id).values(balance=User.balance + amount))
        db.add(PartnerTransaction(user_id=user_id, amount=amount, type=PartnerTxType.TO_BALANCE,
                                  description="Перевод на рекламный баланс"))
        add_transaction(db, user_id=user_id, amount=amount, type=TransactionType.DEPOSIT,
                        description="Перевод заработка партнёра на рекламный баланс")
        db.commit()


def _pending_payout(db: Session, payout_id: int) -> Payout:
    payout = db.get(Payout, payout_id)
    if payout is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Заявка на выплату не найдена")
    if payout.status != PayoutStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Заявка уже обработана")
    return payout


def _close(db: Session, payout_id: int, new_status: PayoutStatus, note: str | None) -> bool:
    """Переводит заявку из pending в new_status. False — её уже обработал параллельный запрос."""
    return bool(db.execute(
        update(Payout).where(Payout.id == payout_id, Payout.status == PayoutStatus.PENDING)
        .values(status=new_status, admin_note=note, processed_at=datetime.now(timezone.utc))
    ).rowcount)


def mark_paid(db: Session, payout_id: int, note: str | None) -> Payout:
    _pending_payout(db, payout_id)
    db.rollback()
    with write_lock():
        if not _close(db, payout_id, PayoutStatus.PAID, note):
            db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Заявка уже обработана")
        db.commit()
    return db.get(Payout, payout_id)


def reject_payout(db: Session, payout_id: int, note: str | None) -> Payout:
    """Отклонение: сумма заявки возвращается на earnings_balance партнёра."""
    payout = _pending_payout(db, payout_id)
    user_id, amount = payout.user_id, payout.amount
    db.rollback()
    with write_lock():
        if not _close(db, payout_id, PayoutStatus.REJECTED, note):
            db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Заявка уже обработана")
        db.execute(update(User).where(User.id == user_id)
                   .values(earnings_balance=User.earnings_balance + amount))
        db.add(PartnerTransaction(user_id=user_id, amount=amount, type=PartnerTxType.PAYOUT_RETURN,
                                  payout_id=payout_id, description=f"Заявка на выплату #{payout_id} отклонена"))
        db.commit()
    return db.get(Payout, payout_id)
