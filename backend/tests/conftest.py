import importlib.util
import os
import subprocess
import sys

import pytest

# Тесты не должны зависеть от локального backend/.env
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdefghijklmnop")
# Тесты НИКОГДА не должны трогать рабочую базу: если запрос по ошибке пройдёт мимо
# фикстуры db, он попадёт в пустую БД в памяти и упадёт, а не изменит app.db
os.environ["DATABASE_URL"] = "sqlite://"
# Ключ Anthropic из backend/.env в тестах не используется: ни один тест не должен
# обращаться к настоящему API (это платно). AI-тесты включают его подменой модели
for _key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
    os.environ[_key] = ""
# Минимальная стоимость bcrypt: в тестах стойкость к перебору не нужна, а скорость — да
os.environ.setdefault("BCRYPT_ROUNDS", "4")

# Частая ошибка: venv не активирован, и `pytest` из PATH запускает другой Python
# без зависимостей проекта. Тогда перезапускаем те же тесты через Python из backend/venv.
_VENV_PYTHON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "venv", "Scripts", "python.exe"
)
_DEPS_MISSING = importlib.util.find_spec("sqlalchemy") is None


def pytest_configure(config):
    if not _DEPS_MISSING:
        return
    if not os.path.exists(_VENV_PYTHON) or os.environ.get("_ADP_PYTEST_RELAUNCHED"):
        pytest.exit(
            f"\nТесты запущены не тем Python: {sys.executable}\n"
            "В нём нет зависимостей проекта. Запустите из папки backend:\n"
            "    .\\venv\\Scripts\\python.exe -m pytest\n"
            "или сначала активируйте venv: .\\venv\\Scripts\\Activate.ps1",
            returncode=4,
        )
    # pytest перехватывает вывод; без паузы перехвата результаты перезапуска потерялись бы
    capman = config.pluginmanager.getplugin("capturemanager")
    if capman:
        capman.suspend_global_capture(in_=True)
    print(f"\n[conftest] Тесты запущены не тем Python ({sys.executable}).\n"
          f"[conftest] Перезапускаю через {_VENV_PYTHON}\n", flush=True)
    result = subprocess.run(
        [_VENV_PYTHON, "-m", "pytest", *config.invocation_params.args],
        cwd=config.invocation_params.dir,
        env={**os.environ, "_ADP_PYTEST_RELAUNCHED": "1"},  # защита от бесконечного перезапуска
    )
    os._exit(result.returncode)  # завершаем «чужой» pytest с кодом настоящего прогона


# Импорты проекта — внутри фикстур: без зависимостей conftest всё равно должен загрузиться,
# чтобы сработал перезапуск выше
@pytest.fixture
def db():
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base

    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        # Прогон на PostgreSQL: TEST_DATABASE_URL=postgresql+psycopg2://.../пустая_тестовая_бд
        engine = create_engine(url)
        Base.metadata.drop_all(engine)
    else:
        # StaticPool: одно соединение на все потоки, иначе каждый поток видит свою пустую БД в памяти
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )

        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def client(db):
    """HTTP-клиент к приложению, работающий с тестовой БД в памяти."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(db):
    """Заголовок авторизации администратора (может создавать площадки, пополнять, модерировать)."""
    from app.auth import create_access_token
    from app.models import User, UserRole

    admin = User(email="admin@example.com", hashed_password="x", role=UserRole.ADMIN)
    db.add(admin)
    db.commit()
    return {"Authorization": f"Bearer {create_access_token(admin.id)}"}


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    # Сохраняет результат шага теста в item.rep_call — по нему e2e-тесты делают скриншоты при сбое
    outcome = yield
    rep = outcome.get_result()
    setattr(item, "rep_" + rep.when, rep)
