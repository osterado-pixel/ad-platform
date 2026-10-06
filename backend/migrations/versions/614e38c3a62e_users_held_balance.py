"""users held balance

Revision ID: 614e38c3a62e
Revises: ca06177f82f5
Create Date: 2026-10-06 14:31:56.514899

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '614e38c3a62e'
down_revision: Union[str, Sequence[str], None] = 'ca06177f82f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """users.held_balance — замороженная сумма (резерв под выполняющиеся AI-генерации)."""
    if op.get_bind().dialect.name == 'sqlite':
        # SQLite добавляет колонку с CHECK напрямую — без пересоздания таблицы users
        # (пересоздание дублирует ограничения, см. миграцию 1a0c406cfb46)
        op.execute("ALTER TABLE users ADD COLUMN held_balance NUMERIC(12, 2) DEFAULT '0' NOT NULL "
                   "CONSTRAINT ck_users_held_balance_nonneg CHECK (held_balance >= 0)")
    else:
        op.add_column('users', sa.Column('held_balance', sa.Numeric(precision=12, scale=2),
                                         server_default='0', nullable=False))
        op.create_check_constraint('ck_users_held_balance_nonneg', 'users', 'held_balance >= 0')
    # Резервы под задачи, которые выполняются прямо сейчас (ai_spend, ещё не рассчитанные по факту)
    op.execute(
        "UPDATE users SET held_balance = COALESCE((SELECT SUM(t.amount) FROM ai_tasks a "
        "JOIN transactions t ON t.id = a.transaction_id "
        "WHERE a.user_id = users.id AND a.status = 'processing'), 0)"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != 'sqlite':
        op.drop_constraint('ck_users_held_balance_nonneg', 'users', type_='check')
    op.drop_column('users', 'held_balance')
