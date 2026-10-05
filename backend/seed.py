"""Тестовые данные для разработки и демонстрации.

    python seed.py                                       # локально (из папки backend)
    docker compose exec api python seed.py               # в Docker

Создаёт рекламодателя advertiser@example.com / password123 с балансом, площадку и активную
кампанию со статистикой. Повторный запуск ничего не дублирует.
Пароль известен всем — не запускайте на боевом сервере.
"""
import sys
from datetime import datetime, timezone
from decimal import Decimal

# Эмодзи и кириллица в консоли Windows при перенаправлении вывода не должны ронять скрипт
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import inspect, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth import get_password_hash  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.ledger import add_transaction  # noqa: E402
from app.models import (  # noqa: E402
    Campaign, CampaignDailyStat, CampaignStatus, Placement, TransactionType, User,
)

EMAIL = "advertiser@example.com"
PASSWORD = "password123"
PLACEMENT_CODE = "habr_main_banner"
PRICE_PER_CLICK = Decimal("15.00")
CLICKS, IMPRESSIONS = 8, 120
DEPOSIT = Decimal("620.00")  # 620 − 8 кликов × 15 = 500 на балансе


def migrate() -> None:
    """Схема — через миграции Alembic, а не create_all: иначе следующий
    `alembic upgrade head` упадёт с «table already exists»."""
    from alembic import command
    from alembic.config import Config
    from pathlib import Path

    command.upgrade(Config(str(Path(__file__).with_name("alembic.ini"))), "head")


def seed(db: Session) -> bool:
    """Заполняет базу. False — данные уже есть (повторный запуск ничего не меняет)."""
    if db.scalar(select(User).where(User.email == EMAIL)):
        return False

    user = User(email=EMAIL, hashed_password=get_password_hash(PASSWORD))
    db.add(user)

    # Площадка может уже существовать (создана вручную) — тогда используем её
    placement = db.scalar(select(Placement).where(Placement.code_identifier == PLACEMENT_CODE))
    if placement is None:
        placement = Placement(name="Главный баннер на Хабре", code_identifier=PLACEMENT_CODE,
                              price_per_click=PRICE_PER_CLICK)
        db.add(placement)
    db.flush()  # получить id

    campaign = Campaign(
        user_id=user.id, placement_id=placement.id,
        title="Продвижение FastAPI Курса", target_url="https://fastapi.tiangolo.com/",
        status=CampaignStatus.ACTIVE, impressions_count=IMPRESSIONS, clicks_count=CLICKS,
    )
    db.add(campaign)
    db.flush()

    spend = PRICE_PER_CLICK * CLICKS
    # Баланс и журнал согласованы: баланс = пополнения − списания
    user.balance = DEPOSIT - spend
    add_transaction(db, user_id=user.id, amount=DEPOSIT, type=TransactionType.DEPOSIT,
                    description="Стартовое пополнение кошелька")
    add_transaction(db, user_id=user.id, amount=spend, type=TransactionType.CLICK_SPEND,
                    campaign_id=campaign.id,
                    description=f"Списание за {CLICKS} кликов по кампании #{campaign.id}")
    # Те же показы и клики — в статистике по дням (иначе «Обзор» показал бы нули)
    db.add(CampaignDailyStat(campaign_id=campaign.id, day=datetime.now(timezone.utc).date(),
                             impressions=IMPRESSIONS, clicks=CLICKS, spend=spend))
    return True


def main() -> int:
    if "users" not in inspect(engine).get_table_names():
        print("Таблиц нет — применяю миграции...")
        migrate()

    db = SessionLocal()
    try:
        print("🌱 Начинаем сидинг базы данных...")
        created = seed(db)
        if not created:
            print("⚠️ База данных уже содержит сид-данные — ничего не изменено")
            return 0
        db.commit()  # одной транзакцией: при сбое база не останется заполненной наполовину

        user = db.scalar(select(User).where(User.email == EMAIL))
        campaign = db.scalar(select(Campaign).where(Campaign.user_id == user.id))
        print("✅ Сидинг успешно завершён!")
        print("---------------------------------------")
        print(f"Пользователь: {EMAIL} / {PASSWORD}")
        print(f"Баланс: {user.balance}")
        print(f"Площадка: {PLACEMENT_CODE} (цена клика: {PRICE_PER_CLICK})")
        print(f"Кампания ID: #{campaign.id} ({campaign.title})")
        print("---------------------------------------")
        print("Модерация и площадки — у администратора: python -m app.cli create-admin email")
        return 0
    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка во время сидинга: {e}", file=sys.stderr)
        return 1  # ненулевой код: сбой заметят в Docker/CI
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
