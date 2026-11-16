# ---- Build stage: compile wheels once so the runtime image needs no compilers ----
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---- Runtime stage: small image, non-root user ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN useradd --create-home --uid 10001 dq
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts

USER dq
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Same image runs the API, the Celery worker and Celery beat (see docker-compose.yml).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
