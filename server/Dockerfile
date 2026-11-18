FROM python:3.12-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

ENV UV_NO_CACHE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 MEMORY_DATA_DIR=/tmp/data PATH=/app/.venv/bin:$PATH

WORKDIR /app

COPY --from=build /app/.venv .venv
COPY memory memory
COPY migrations migrations
COPY models models
COPY evals evals
COPY prices.json ./

CMD ["sh", "-c", "exec uvicorn memory.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
