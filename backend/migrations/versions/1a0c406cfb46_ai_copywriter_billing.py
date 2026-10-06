"""ai copywriter billing

Тип операции ai_spend (оплата AI-генерации) и журнал AI-запросов ai_logs.

Revision ID: 1a0c406cfb46
Revises: 30f1da2e5e97
Create Date: 2026-10-06 11:31:10.359149

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1a0c406cfb46'
down_revision: Union[str, Sequence[str], None] = '30f1da2e5e97'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_TYPES = ('deposit', 'click_spend', 'refund')
NEW_TYPES = OLD_TYPES + ('ai_spend',)


def _transactions(types: tuple[str, ...]) -> sa.Table:
    """Таблица transactions целиком — для пересоздания в SQLite (ALTER CONSTRAINT там нет)."""
    return sa.Table(
        'transactions', sa.MetaData(),
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('type', sa.Enum(*types, name='transaction_type', native_enum=False, create_constraint=True,
                                  length=11), nullable=False),
        sa.Column('campaign_id', sa.Integer(), sa.ForeignKey('campaigns.id', ondelete='SET NULL'), nullable=True),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'),
                  nullable=False),
        sa.CheckConstraint('amount > 0', name='ck_transactions_amount_positive'),
        sa.Index('ix_transactions_campaign_id', 'campaign_id'),
        sa.Index('ix_transactions_user_id_id', 'user_id', 'id'),
    )


def _set_types(types: tuple[str, ...]) -> None:
    if op.get_bind().dialect.name == 'sqlite':
        # Пересоздаём по точному описанию: заодно уходят дубли CHECK, оставшиеся от прежних
        # batch-миграций (SQLite копировал ограничение transaction_type при каждом пересоздании)
        with op.batch_alter_table('transactions', copy_from=_transactions(types), recreate='always'):
            pass
    else:
        op.drop_constraint('transaction_type', 'transactions', type_='check')
        values = ", ".join(f"'{t}'" for t in types)
        op.create_check_constraint('transaction_type', 'transactions', f"type IN ({values})")


def upgrade() -> None:
    _set_types(NEW_TYPES)
    op.create_table(
        'ai_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('prompt_type', sa.String(length=50), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False),
        sa.Column('completion_tokens', sa.Integer(), nullable=False),
        sa.Column('total_tokens', sa.Integer(), nullable=False),
        sa.Column('cost', sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column('charged', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('transaction_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'),
                  nullable=False),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ai_logs_user_id_id', 'ai_logs', ['user_id', 'id'], unique=False)


def downgrade() -> None:
    # Операции ai_spend старая схема не допускает: откат невозможен, пока они есть в журнале
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM transactions WHERE type = 'ai_spend'")):
        raise RuntimeError("В transactions есть операции ai_spend — откат миграции удалил бы историю денег")
    op.drop_index('ix_ai_logs_user_id_id', table_name='ai_logs')
    op.drop_table('ai_logs')
    _set_types(OLD_TYPES)
