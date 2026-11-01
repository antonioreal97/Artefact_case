FROM python:3.12-slim

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
# read-only into the one-off `seed` compose service only, and is excluded by .dockerignore.
RUN uv sync --frozen --no-dev \
    && python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')" \
    && chmod -R a+rX /opt/tiktoken-cache \
    && useradd --create-home --uid 10001 appuser
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
