FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Сначала зависимости — слой кешируется, пока не меняется requirements.txt
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Не от root
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000

# FORWARDED_ALLOW_IPS: от кого принимать X-Forwarded-For (реальный IP посетителя).
# По умолчанию — никому: иначе любой подставит чужой IP и обойдёт защиту от повторных кликов.
# За nginx/балансировщиком укажите его адрес (или "*" во внутренней сети Docker).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-server-header --workers ${WEB_WORKERS:-2} --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"
