FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Runtime dependencies only (requirements.txt also lists pytest for development).
COPY requirements.txt .
RUN grep -v '^pytest' requirements.txt > requirements.runtime.txt \
    && pip install -r requirements.runtime.txt

COPY bot.py .

RUN useradd --no-create-home --shell /usr/sbin/nologin bot
USER bot

# TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY and ALLOWED_USER_IDS are supplied at
# run time (never baked into the image).
CMD ["python", "bot.py"]
