import os
import secrets
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
# Служебные данные приложения (сгенерированный ключ). В Docker — том /app/data
DATA_DIR = Path(os.environ.get("DATA_DIR") or BASE_DIR / "data")


def _load_or_create_secret_key() -> str:
    """Ключ из DATA_DIR/secret_key; при первом запуске — случайный, сохраняется туда же.

    Страховка на случай потери .env: платформа не падает и не переходит на известный всем
    ключ из примеров — у каждой установки свой, и после перезапуска он тот же.
    """
    path = DATA_DIR / "secret_key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Пишем во временный файл и атомарно «публикуем» через os.link: если несколько процессов
    # сервера стартуют одновременно, ключ создаст первый, остальные прочитают его же
    # (иначе у процессов были бы разные ключи и токены «терялись» бы между ними)
    tmp = path.with_name(f".secret_key.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    try:
        tmp.chmod(0o600)  # читать может только владелец (на Windows игнорируется)
    except OSError:
        pass
    try:
        os.link(tmp, path)
    except FileExistsError:
        pass  # другой процесс успел первым — берём его ключ
    finally:
        tmp.unlink(missing_ok=True)
    return path.read_text(encoding="utf-8").strip()


class Settings(BaseSettings):
    # По умолчанию SQLite-файл backend/app.db (не зависит от текущей папки).
    # Для PostgreSQL задайте в backend/.env:
    # DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/ad_db
    database_url: str = f"sqlite:///{(BASE_DIR / 'app.db').as_posix()}"

    # Ключ подписи токенов: из SECRET_KEY (backend/.env), а если не задан — сгенерированный
    # и сохранённый в DATA_DIR/secret_key. В коде и примерах ключ не хранится
    secret_key: str = ""
    # ALGORITHM — имя из учебной инструкции, то же самое
    jwt_algorithm: str = Field(default="HS256", validation_alias=AliasChoices("JWT_ALGORITHM", "ALGORITHM"))
    access_token_expire_minutes: int = 60 * 24  # Токен валиден 24 часа
    # Стоимость bcrypt: 12 ≈ 0.25 с на хеш — защита от перебора. В тестах ставим 4
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)

    # Защита от перебора паролей и массовой регистрации
    login_max_failures_per_email: int = Field(default=5, ge=1)
    login_max_failures_per_ip: int = Field(default=20, ge=1)
    login_window_minutes: int = Field(default=15, ge=1)
    register_max_per_ip_per_hour: int = Field(default=10, ge=1)

    # AI-проверка объявлений (Claude). Нет ключа — проверка выключена, модерация только ручная
    anthropic_api_key: str = ""
    ai_model: str = "claude-opus-5-5"
    ai_timeout_seconds: float = Field(default=30, gt=0)
    # Автоматически отклонять, если модель уверенно нашла нарушение (verdict=reject, risk=high).
    # По умолчанию выключено: решение всегда за модератором, модель лишь подсказывает
    ai_auto_reject: bool = False
    # Ключ Google Gemini API (Google AI Studio, aistudio.google.com/apikey)
    gemini_api_key: str = ""

    # Повторный клик с того же IP по той же кампании в этом окне не оплачивается
    click_dedup_minutes: int = Field(default=10, gt=0)

    # С каких доменов фронтенд (React/Vue/Next.js) может обращаться к API из браузера,
    # через запятую. По умолчанию — локальные серверы разработки. На сервере укажите домен
    # фронтенда: CORS_ORIGINS=https://app.example.com. "*" — с любых (не рекомендуется).
    # Выдача рекламы (/api/v1/ad/*, /widget.js) доступна с любых сайтов независимо от этого.
    cors_origins: str = ",".join(
        f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (3000, 5173, 8080)
    )

    # Допустимые значения заголовка Host через запятую, например "ads.example.com".
    # "*" — любые (по умолчанию). На сервере защищает от подмены Host в ссылках клика
    allowed_hosts: str = "*"

    @property
    def GEMINI_API_KEY(self) -> str:  # noqa: N802 — имя из учебной инструкции, то же, что gemini_api_key
        return self.gemini_api_key

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        hosts = [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]
        if hosts and "*" not in hosts:
            # Внутренние адреса — всегда: по ним ходит проверка здоровья контейнера (HEALTHCHECK).
            # Снаружи по ним не обратиться: прокси принимает запросы только для своего домена
            hosts += [h for h in ("127.0.0.1", "localhost") if h not in hosts]
        return hosts

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore", populate_by_name=True)

    @model_validator(mode="after")
    def _secret_key(self) -> "Settings":
        if not self.secret_key.strip():
            self.secret_key = _load_or_create_secret_key()
        if len(self.secret_key) < 32:
            raise ValueError("SECRET_KEY должен быть не короче 32 символов")
        return self


settings = Settings()
