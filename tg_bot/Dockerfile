FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /srv/tg_bot

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

RUN useradd --system --uid 10001 bot && chown -R bot /srv/tg_bot
USER bot

CMD ["sh", "-c", "aerich upgrade && python -m app"]
