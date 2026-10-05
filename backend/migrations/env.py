from logging.config import fileConfig

from alembic import context

from app.config import settings
from app.database import Base, engine, is_sqlite
import app.models  # noqa: F401  регистрирует модели в Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    # CHECK для enum создаётся самим типом колонки sa.Enum(..., create_constraint=True).
    # Alembic с SQLAlchemy 2.1 этого не распознаёт и дописывает его ещё раз отдельными строками —
    # SQLite такие дубли молча принимает, а PostgreSQL падает ("constraint already exists")
    if type_ == "check" and getattr(obj, "_type_bound", False):
        return None
    return False  # остальное — как обычно


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=is_sqlite,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        if is_sqlite:
            # При batch-миграции SQLite пересоздаёт таблицу (DROP + CREATE).
            # С включёнными FK DROP TABLE users каскадно удалил бы все кампании.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            # PRAGMA неявно открыл транзакцию: закрываем, иначе Alembic не закоммитит миграцию
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=is_sqlite,  # SQLite не умеет ALTER COLUMN
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
