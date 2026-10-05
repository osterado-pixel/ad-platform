import threading
from contextlib import nullcontext

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

is_sqlite = settings.database_url.startswith("sqlite")

engine = create_engine(
    settings.database_url,
    # timeout: сколько секунд SQLite ждёт, пока другой запрос освободит запись
    connect_args={"check_same_thread": False, "timeout": 15} if is_sqlite else {},
    # Postgres: проверять соединение из пула перед использованием (после рестарта БД)
    pool_pre_ping=not is_sqlite,
)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        # SQLite по умолчанию не проверяет внешние ключи
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
        # WAL: чтение не блокируется записью — /serve не ждёт, пока пишутся клики
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        # В WAL это безопасно (без риска повредить БД) и сильно ускоряет commit
        dbapi_conn.execute("PRAGMA synchronous=NORMAL")


# SQLite допускает одного писателя. При конфликте он ждёт с нарастающими паузами,
# поэтому под нагрузкой отдельные запросы висят секундами. Очередь в Python
# пропускает писателей по одному без пауз. Для PostgreSQL не нужна.
_sqlite_write_lock = threading.Lock()


def write_lock():
    return _sqlite_write_lock if is_sqlite else nullcontext()


SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


# Зависимость (Dependency) для получения сессии БД в эндпоинтах API
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
