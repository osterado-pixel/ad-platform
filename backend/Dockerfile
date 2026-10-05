# 1. Базовый образ
#    Python 3.14 — на этой версии проект проверен тестами (в инструкции 3.11)
FROM python:3.14-slim

# 2. Не записываем .pyc на диск и выводим логи сразу, без буферизации
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 3. Рабочая директория внутри контейнера
WORKDIR /app

# 4. Системные зависимости
#    Не нужны: все пакеты ставятся готовыми сборками (wheels). build-essential и curl
#    из инструкции увеличили бы образ на сотни МБ и добавили бы пакетов с уязвимостями

# 5. Зависимости — отдельным слоем: он кешируется, пока не меняется requirements.txt
COPY requirements.txt .
RUN pip install -r requirements.txt

# 6. Исходный код приложения (лишнее исключено в .dockerignore: venv, .env, базы, тесты)
COPY . .

#    Запуск не от root: при взломе приложения у атакующего не будет прав root в контейнере
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

# 7. Порт приложения
EXPOSE 8000

# 8. Запуск: сначала миграции базы (без них на чистой базе — «no such table»), затем Uvicorn.
#    --proxy-headers / FORWARDED_ALLOW_IPS: реальный IP посетителя за прокси (защита от
#    повторных кликов). По умолчанию заголовок не принимается ни от кого: иначе любой
#    подставит чужой IP. За nginx/Caddy укажите адрес прокси (или "*" во внутренней сети Docker).
#    --no-server-header: не раскрывать, какой сервер отвечает
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-server-header --workers ${WEB_WORKERS:-2} --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]

#    Проверка здоровья: Docker видит, что приложение действительно отвечает
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"
