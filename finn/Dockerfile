# One image: the built web app and the API together. The database ships inside it,
# so a fresh deploy opens with real data before any job has run.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra push --python 3.12
COPY finn/ finn/
COPY data/finn.db data/finn.db
COPY --from=web /web/dist web/dist
# One malloc arena per core keeps threads from holding on to freed PDF memory on a 512 MB host.
ENV PORT=8000 FINN_REFRESH_MINUTES=30 MALLOC_ARENA_MAX=2
# Spaces and similar hosts run the container as a non-root user.
RUN mkdir -p data/cache && chmod -R a+rwX data
EXPOSE 8000
CMD ["sh", "-c", "uv run --frozen --no-dev --no-sync --extra push uvicorn finn.api:app --host 0.0.0.0 --port ${PORT}"]
