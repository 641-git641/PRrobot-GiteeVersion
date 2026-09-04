FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY review-rules.md ./review-rules.md

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 robot \
    && mkdir -p /app/data \
    && chown -R robot:robot /app

USER robot

EXPOSE 8090

CMD ["uvicorn", "reviewbot.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8090"]
