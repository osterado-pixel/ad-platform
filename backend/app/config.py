from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/


class Settings(BaseSettings):
    # По умолчанию SQLite-файл backend/app.db (не зависит от текущей папки).
    # Для PostgreSQL задайте в backend/.env:
    # DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/ad_db
    database_url: str = f"sqlite:///{(BASE_DIR / 'app.db').as_posix()}"

    # Обязателен: задаётся в backend/.env (SECRET_KEY=...), в коде не хранится
    secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # Токен валиден 24 часа
    # Стоимость bcrypt: 12 ≈ 0.25 с на хеш — защита от перебора. В тестах ставим 4
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)

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
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")


settings = Settings()
