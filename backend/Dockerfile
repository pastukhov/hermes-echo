# syntax=docker/dockerfile:1

# Voice Gateway backend image (ТЗ §37 — Backend deployment).
#
# Build from the REPO ROOT (the build context must expose `backend/` as a
# package so the `backend.src.voice_gateway.app:app` import path resolves):
#
#     docker build -f backend/Dockerfile .
#
# or, equivalently, via the repo-root docker-compose.yml (child card §37).
#
# The app reads no VOICE_BIND_* itself; those are consumed by the uvicorn
# launch command below (ТЗ §36: VOICE_BIND_HOST / VOICE_BIND_PORT). The
# archive root env var is read by the app as ARCHIVE_ROOT (current code).

# ---- build stage: resolve + install runtime deps into an isolated prefix --
FROM python:3.12-slim AS build

WORKDIR /build

# Only what the gateway needs to run (ТЗ §37: install only necessary deps).
# Versions are pinned in backend/requirements.txt for reproducible builds.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# ---- runtime stage --------------------------------------------------------
FROM python:3.12-slim

# Non-root user (ТЗ §37: container must not run as root when avoidable).
RUN groupadd --system app \
    && useradd --system --gid app --create-home app

# Runtime configuration defaults (ТЗ §36). All overridable via `-e`/compose.
#   VOICE_BIND_HOST / VOICE_BIND_PORT -> uvicorn bind address (launch cmd).
#   ARCHIVE_ROOT                      -> where the app writes turn archives
#                                        (the env var name the current code
#                                         actually reads; §36 calls it
#                                         ARCHIVE_PATH — reconcile in config).
#   PYTHONDONTWRITEBYTECODE           -> keep the read-only /app tree clean.
ENV VOICE_BIND_HOST=0.0.0.0 \
    VOICE_BIND_PORT=8000 \
    ARCHIVE_ROOT=/data/archive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# Pull the installed deps in from the build stage (no pip cache in runtime).
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin/uvicorn /usr/local/bin/uvicorn

WORKDIR /app

# Application code — `backend/` is a namespace package; the gateway is
# `backend.src.voice_gateway.app:app`. backend/prompts/* is required at
# runtime (ТЗ §23 system prompt).
COPY --chown=app:app backend/ /app/backend/

# Data mount targets (ТЗ §37 volumes): /data/archive + /data/obsidian.
# Pre-create and hand ownership to the non-root user so a bind mount or a
# fresh named volume does not break archive writes.
RUN mkdir -p /data/archive /data/obsidian \
    && chown -R app:app /data

# Drop privileges: the server process runs as the non-root `app` user.
USER app

# Health/telemetry endpoints live on the configured port.
EXPOSE 8000

# Launch command (ТЗ §36): uvicorn bound to VOICE_BIND_HOST:VOICE_BIND_PORT.
# `exec` replaces the shell so uvicorn IS PID 1 and receives SIGTERM/SIGINT
# directly (graceful shutdown under compose down / k8s; keeps the shell-form
# env-var expansion the launch command needs).
CMD ["sh", "-c", "exec uvicorn backend.src.voice_gateway.app:app --host ${VOICE_BIND_HOST:-0.0.0.0} --port ${VOICE_BIND_PORT:-8000}"]
