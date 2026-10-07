# Telegram-бот Ad Platform (aiogram). Long polling: открытый порт и HTTPS не нужны.
#
#   docker build -t ad-platform-bot .
#   docker run -e BOT_TOKEN=... -e TELEGRAM_BOT_SECRET=... -e API_URL=http://api:8000 ad-platform-bot
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
# Зависимости — отдельным слоем: кешируется, пока не меняется requirements.txt
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

# Не от root: при уязвимости у злоумышленника меньше прав в контейнере
RUN useradd --create-home --uid 10001 bot
USER bot

CMD ["python", "main.py"]
