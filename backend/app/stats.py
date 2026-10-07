"""Счётчики статистики по дням (показы, клики, расход)."""
import time
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import CampaignDailyStat, PlacementDailyStat


def utc_today() -> date:
    # time.time(), а не datetime.now(): в тестах время подменяется в одном месте
    return datetime.fromtimestamp(time.time(), tz=timezone.utc).date()


def bump_daily(db: Session, campaign_id: int, *, impressions: int = 0, clicks: int = 0,
               spend: Decimal = Decimal("0")) -> None:
    """Атомарно прибавляет к строке за сегодня (создаёт её при первом событии дня).

    INSERT ... ON CONFLICT DO UPDATE есть и в SQLite, и в PostgreSQL, но у SQLAlchemy
    для каждого диалекта свой insert().
    """
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert

    table = CampaignDailyStat.__table__
    stmt = insert(table).values(
        campaign_id=campaign_id, day=utc_today(),
        impressions=impressions, clicks=clicks, spend=spend,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.campaign_id, table.c.day],
        set_={
            "impressions": table.c.impressions + stmt.excluded.impressions,
            "clicks": table.c.clicks + stmt.excluded.clicks,
            "spend": table.c.spend + stmt.excluded.spend,
        },
    )
    db.execute(stmt)


def bump_placement_daily(db: Session, placement_id: int, *, impressions: int = 0, clicks: int = 0,
                         spend: Decimal = Decimal("0")) -> None:
    """То же для площадки: её отчёт включает и кампании на всю сеть (у них нет своей площадки)."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert

    table = PlacementDailyStat.__table__
    stmt = insert(table).values(
        placement_id=placement_id, day=utc_today(),
        impressions=impressions, clicks=clicks, spend=spend,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.placement_id, table.c.day],
        set_={name: table.c[name] + stmt.excluded[name] for name in ("impressions", "clicks", "spend")},
    )
    db.execute(stmt)
