FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY src ./src
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./
# No customer data in the image (review M-7): the seed data directory is bind-mounted
# read-only into the one-off `seed` and `test` compose services, and is excluded by .dockerignore.
RUN uv sync --frozen --no-dev \
    && python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')" \
    && chmod -R a+rX /opt/tiktoken-cache \
    && useradd --create-home --uid 10001 appuser
USER appuser
# Development dependencies and repository fixtures live only in this opt-in target.
FROM base AS test
USER root
RUN uv sync --frozen --group dev
COPY --chown=appuser:appuser tests ./tests
COPY --chown=appuser:appuser docs ./docs
COPY --chown=appuser:appuser evals ./evals
COPY --chown=appuser:appuser examples ./examples
COPY README.md SECURITY.md Dockerfile docker-compose.yml .dockerignore .env.example ./
RUN mkdir -p /app/evals/results /app/.pytest_cache \
    && chown -R appuser:appuser /app/evals/results /app/.pytest_cache
USER appuser
CMD ["pytest", "-q"]

# Keep runtime last: ordinary builds never ship tests or development dependencies.
FROM base AS runtime
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
