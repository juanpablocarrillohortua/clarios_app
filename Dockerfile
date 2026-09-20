# =============================================================================
# Clarios Energy - multi-stage image
#
# Stage layout:
#   base    - shared runtime foundation and the non-root user
#   builder - resolves and installs dependencies into a self-contained venv
#   runtime - copies that venv plus the source; carries no build tooling
#   api     - entry point for the FastAPI service
#   dash    - entry point for the Plotly Dash service
#
# The venv is built in a separate stage so neither uv nor any compiler reaches
# the final image. Dependencies are installed before the source is copied, so a
# source edit does not invalidate the dependency layer.
# =============================================================================

ARG PYTHON_VERSION=3.12

# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS base
# -----------------------------------------------------------------------------
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:${PATH}"

# curl is required by the container healthchecks; libpq5 by psycopg.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        curl \
        libpq5 \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Fixed uid/gid so a bind-mounted volume has predictable ownership.
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash appuser

WORKDIR /app

# -----------------------------------------------------------------------------
FROM base AS builder
# -----------------------------------------------------------------------------
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Build toolchain for any dependency without a manylinux wheel. Present only in
# this stage; the runtime image never sees it.
RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer: cached until the lockfile itself changes.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Project layer.
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# -----------------------------------------------------------------------------
FROM base AS runtime
# -----------------------------------------------------------------------------
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser migrations/ ./migrations/
COPY --chown=appuser:appuser config/ ./config/
COPY --chown=appuser:appuser scripts/ ./scripts/
COPY --chown=appuser:appuser pyproject.toml uv.lock README.md LICENSE ./

RUN chmod +x ./scripts/*.sh 2>/dev/null || true

USER appuser

# tini reaps zombies and forwards signals, so `docker compose down` stops the
# service promptly instead of waiting out the 10s kill timeout.
ENTRYPOINT ["/usr/bin/tini", "--"]

# -----------------------------------------------------------------------------
FROM runtime AS api
# -----------------------------------------------------------------------------
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl --fail --silent --show-error http://localhost:8000/health || exit 1

CMD ["uvicorn", "energy.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]

# -----------------------------------------------------------------------------
FROM runtime AS dash
# -----------------------------------------------------------------------------
EXPOSE 8050

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl --fail --silent --show-error http://localhost:8050/ || exit 1

CMD ["python", "-m", "energy.dashboards.app"]
